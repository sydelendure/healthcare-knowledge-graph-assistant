from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.graph_queries import (
    find_doctors_with_graph_hopping,
    get_doctors_by_specialty,
    get_doctors_by_hospital,
    get_hospitals_by_district,
    get_all_hospitals,
    get_doctor_by_name,
    check_doctor_specialty_with_graph_hopping,
    find_closest_hospitals,
)

from backend.llm import understand_question, fuzzy_graph_fallback


app = FastAPI(
    title="Healthcare Knowledge Graph API",
    description="API for querying the healthcare knowledge graph with multi-hop graph traversal.",
    version="1.0.0",
)


@app.on_event("startup")
def startup_checks():
    print("Running startup configuration checks...")
    try:
        from app.database import Neo4jDatabase
        db = Neo4jDatabase()
        if db.verify_connection():
            print("Startup check: Neo4j connection verified successfully.")
        else:
            print("Startup check warning: Neo4j connectivity check failed.")
        db.close()
    except Exception as e:
        print(f"Startup check warning: {e}")


class QuestionRequest(BaseModel):
    question: str


@app.get("/")
def root():
    return {
        "message": "Healthcare Knowledge Graph API is running"
    }


@app.get("/doctors")
def doctors(
    specialty: str,
    district: str = None
):
    specialty = specialty.strip()

    if district:
        hop_data = find_doctors_with_graph_hopping(
            specialty,
            district.strip()
        )
        results = hop_data["results"]
    else:
        results = get_doctors_by_specialty(specialty)

    if not results:
        raise HTTPException(
            status_code=404,
            detail="No doctors found for the given specialty."
        )

    return {
        "count": len(results),
        "results": results
    }


@app.get("/doctors/hospital")
def doctors_by_hospital(
    hospital_name: str
):
    hospital_name = hospital_name.strip()

    results = get_doctors_by_hospital(
        hospital_name
    )

    if not results:
        raise HTTPException(
            status_code=404,
            detail="No doctors found for the given hospital."
        )

    return {
        "count": len(results),
        "results": results
    }


@app.get("/hospitals")
def hospitals(
    district: str = None
):
    if district:
        results = get_hospitals_by_district(
            district.strip()
        )
    else:
        results = get_all_hospitals()

    if not results:
        raise HTTPException(
            status_code=404,
            detail="No hospitals found."
        )

    return {
        "count": len(results),
        "results": results
    }


@app.get("/hospitals/closest")
def closest_hospitals(
    hospital_name: str,
    specialty: str = None
):
    hospital_name = hospital_name.strip()
    specialty = specialty.strip() if specialty else None
    results = find_closest_hospitals(hospital_name, specialty)
    if not results:
        raise HTTPException(
            status_code=404,
            detail="No connected hospitals found in referral network."
        )
    return {
        "origin_hospital": hospital_name,
        "specialty": specialty,
        "count": len(results),
        "results": results
    }


def create_disambiguation_payload(
    question: str,
    doctor_name: str,
    results: list,
    requested_specialty: str = None,
    requested_hospital: str = None
):
    unique_hosps = list(dict.fromkeys([r.get("hospital") for r in results if r.get("hospital")]))

    cand_list = []
    for r in results:
        doc = r.get("doctor") or doctor_name
        spec = r.get("specialization") or r.get("actual_specialty") or "General Medicine"
        dept = r.get("department") or (f"{spec} Department" if spec else "Clinical Department")
        hosp = r.get("hospital") or "Hospital"
        dist = r.get("district") or ""
        city = r.get("city") or ""

        if len(unique_hosps) == 1:
            suggested_q = f"Tell me about {doc} in {dept} ({spec}) at {hosp}"
        else:
            suggested_q = f"Tell me about {doc} ({spec}) at {hosp}"

        cand_list.append({
            "doctor_id": r.get("doctor_id"),
            "doctor": doc,
            "actual_specialty": spec,
            "specialization": spec,
            "department": dept,
            "hospital": hosp,
            "district": dist,
            "city": city,
            "is_specialty_match": (spec.lower() == requested_specialty.lower()) if requested_specialty else None,
            "suggested_query": suggested_q
        })

    if len(unique_hosps) == 1:
        hosp_name = unique_hosps[0]
        disambiguation_type = "same_hospital"
        clarification_prompt = (
            f"Multiple doctors named '{doctor_name}' were found at {hosp_name}. "
            "Which department and specialization are you looking for?"
        )
    elif len(unique_hosps) == len(results):
        disambiguation_type = "different_hospitals"
        clarification_prompt = (
            f"Multiple doctors named '{doctor_name}' were found across different hospitals. "
            "Which hospital and specialization are you looking for?"
        )
    else:
        disambiguation_type = "mixed"
        clarification_prompt = (
            f"Multiple doctors named '{doctor_name}' were found across multiple hospitals and departments. "
            "Which hospital, department, and specialization are you looking for?"
        )

    return {
        "question": question,
        "intent": "doctor_disambiguation",
        "ambiguous": True,
        "disambiguation_type": disambiguation_type,
        "doctor_name": doctor_name,
        "hospital_name": unique_hosps[0] if len(unique_hosps) == 1 else requested_hospital,
        "requested_specialty": requested_specialty,
        "clarification_prompt": clarification_prompt,
        "message": clarification_prompt,
        "candidates": cand_list,
        "count": len(results),
        "results": results
    }


@app.post("/ask")
def ask_question(request: QuestionRequest):

    question = request.question.strip()

    if not question:
        raise HTTPException(
            status_code=400,
            detail="Question cannot be empty."
        )

    # Step 1: Ask the LLM to understand the question
    try:
        intent_data = understand_question(question)
        print("LLM OUTPUT:", intent_data)
    except Exception as e:
        print("LLM Exception, using fuzzy fallback:", e)
        intent_data = fuzzy_graph_fallback(question)

    intent = intent_data.get("intent") if isinstance(intent_data, dict) else "unknown"
    graph_hop_info = {}

    # Step 2: Execute the appropriate graph query

    if intent in ["doctors_by_specialty_and_location", "doctors_by_specialty_and_district"]:

        specialty = intent_data.get("specialty")
        location = intent_data.get("location") or intent_data.get("district")

        if specialty and location:
            hop_res = find_doctors_with_graph_hopping(
                specialty,
                location
            )
            results = hop_res.get("results", [])
            if hop_res.get("graph_hopped"):
                graph_hop_info = {
                    "graph_hopped": True,
                    "hop_type": hop_res.get("hop_type"),
                    "hop_origin": hop_res.get("hop_origin"),
                    "hop_target": hop_res.get("hop_target"),
                    "hop_distance": hop_res.get("hop_distance"),
                    "total_distance_km": hop_res.get("total_distance_km"),
                    "step_distances": hop_res.get("step_distances", []),
                    "traversal_path": hop_res.get("traversal_path", [])
                }
        elif specialty:
            results = get_doctors_by_specialty(specialty)
        else:
            results = get_all_hospitals()

    elif intent == "doctors_by_specialty":

        specialty = intent_data.get("specialty")
        if specialty:
            results = get_doctors_by_specialty(specialty)
        else:
            results = []

    elif intent == "doctors_by_hospital":

        hospital_name = intent_data.get("hospital_name")
        if hospital_name:
            results = get_doctors_by_hospital(hospital_name)
        else:
            results = get_all_hospitals()

    elif intent == "hospitals_by_district":

        district = intent_data.get("district")
        if district:
            results = get_hospitals_by_district(district)
        else:
            results = get_all_hospitals()

    elif intent == "all_hospitals":

        results = get_all_hospitals()

    elif intent == "doctor_by_name":

        doctor_name = intent_data.get("doctor_name")
        hospital_name = intent_data.get("hospital_name") or intent_data.get("hospital") or intent_data.get("location")
        specialty = intent_data.get("specialty")
        department = intent_data.get("department")
        if doctor_name:
            results = get_doctor_by_name(doctor_name, hospital_name=hospital_name, specialty=specialty, department=department)
            if len(results) > 1:
                return create_disambiguation_payload(
                    question=question,
                    doctor_name=doctor_name,
                    results=results,
                    requested_specialty=specialty,
                    requested_hospital=hospital_name
                )
        else:
            results = []

    elif intent == "doctor_and_specialty_check":

        doctor_name = intent_data.get("doctor_name")
        specialty = intent_data.get("specialty")
        hospital_name = intent_data.get("hospital_name") or intent_data.get("hospital") or intent_data.get("location")
        if doctor_name and specialty:
            check_res = check_doctor_specialty_with_graph_hopping(doctor_name, specialty, hospital_name=hospital_name)
            results = check_res.get("results", [])
            if check_res.get("ambiguous"):
                return create_disambiguation_payload(
                    question=question,
                    doctor_name=check_res.get("doctor_name") or doctor_name,
                    results=check_res.get("candidates", results),
                    requested_specialty=check_res.get("requested_specialty") or specialty,
                    requested_hospital=hospital_name
                )
            if check_res.get("graph_hopped"):
                graph_hop_info = {
                    "graph_hopped": True,
                    "hop_type": check_res.get("hop_type"),
                    "hop_origin": check_res.get("hop_origin"),
                    "hop_target": check_res.get("hop_target"),
                    "is_specialty_match": check_res.get("is_specialty_match"),
                    "doctor_name": check_res.get("doctor_name"),
                    "actual_specialty": check_res.get("actual_specialty"),
                    "requested_specialty": check_res.get("requested_specialty")
                }
            elif check_res.get("is_specialty_match") is not None:
                graph_hop_info = {
                    "is_specialty_match": check_res.get("is_specialty_match"),
                    "doctor_name": check_res.get("doctor_name"),
                    "actual_specialty": check_res.get("actual_specialty"),
                    "requested_specialty": check_res.get("requested_specialty")
                }
        elif doctor_name:
            results = get_doctor_by_name(doctor_name, hospital_name=hospital_name)
            if len(results) > 1:
                return create_disambiguation_payload(
                    question=question,
                    doctor_name=doctor_name,
                    results=results,
                    requested_specialty=None,
                    requested_hospital=hospital_name
                )
        elif specialty:
            results = get_doctors_by_specialty(specialty)
        else:
            results = []

    elif intent == "unknown":

        return {
            "question": question,
            "answer": (
                "I could not determine how to answer this question "
                "using the available healthcare data."
            )
        }

    else:

        return {
            "question": question,
            "answer": (
                "I could not determine how to answer this question "
                "using the available healthcare data."
            )
        }

    # Step 3: Return the graph results
    response_payload = {
        "question": question,
        "intent": intent,
        "count": len(results),
        "results": results
    }
    if graph_hop_info:
        response_payload.update(graph_hop_info)

    return response_payload