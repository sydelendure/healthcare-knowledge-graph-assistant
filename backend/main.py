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


def build_subgraph_from_results(
    intent: str,
    results: list,
    graph_hop_info: dict = None,
    disambiguation: bool = False,
    candidates: list = None
) -> dict:
    nodes = []
    links = []
    node_ids = set()

    def add_node(node_id, label, node_type, color, radius=13, properties=None):
        if not node_id or node_id in node_ids:
            return
        node_ids.add(node_id)
        nodes.append({
            "id": str(node_id),
            "label": str(label),
            "type": node_type,
            "color": color,
            "radius": radius,
            "properties": properties or {}
        })

    def add_link(source_id, target_id, label, properties=None):
        if not source_id or not target_id or source_id == target_id:
            return
        links.append({
            "source": str(source_id),
            "target": str(target_id),
            "label": label,
            "properties": properties or {}
        })

    items = candidates if (disambiguation and candidates) else (results or [])

    # Process items (Doctors, Hospitals, Specialties, Departments, Locations)
    for idx, item in enumerate(items):
        doc_name = item.get("doctor") or item.get("name")
        doc_id = item.get("doctor_id") or (f"doc_{doc_name}_{idx}" if doc_name else None)
        hosp_name = item.get("hospital")
        hosp_id = item.get("hospital_id") or (f"hosp_{hosp_name}" if hosp_name else None)
        spec_name = item.get("specialization") or item.get("actual_specialty")
        spec_id = f"spec_{spec_name}" if spec_name else None
        dept_name = item.get("department")
        dept_id = f"dept_{dept_name}" if dept_name else None
        district = item.get("district")
        city = item.get("city")
        loc_label = district or city
        loc_id = f"loc_{loc_label}" if loc_label else None

        if doc_name and doc_id:
            add_node(doc_id, doc_name, "Doctor", "#10b981", 15, {
                "Doctor": doc_name,
                "Specialization": spec_name or "General",
                "Hospital": hosp_name or "Affiliated Facility",
                "Department": dept_name or "Clinical"
            })

        if hosp_name and hosp_id:
            add_node(hosp_id, hosp_name, "Hospital", "#06b6d4", 17, {
                "Hospital": hosp_name,
                "District": district or "District Area",
                "City": city or ""
            })

        if spec_name and spec_id:
            add_node(spec_id, spec_name, "Speciality", "#8b5cf6", 14, {
                "Specialty": spec_name
            })

        if dept_name and dept_id:
            add_node(dept_id, dept_name, "Department", "#f59e0b", 13, {
                "Department": dept_name
            })

        if loc_label and loc_id:
            add_node(loc_id, loc_label, "Location", "#3b82f6", 14, {
                "Location": loc_label
            })

        # Add relationships
        if doc_id and hosp_id:
            add_link(doc_id, hosp_id, "WORKS_AT")
        if doc_id and spec_id:
            add_link(doc_id, spec_id, "HAS_SPECIALTY")
        if hosp_id and dept_id:
            add_link(hosp_id, dept_id, "HAS_DEPARTMENT")
        if dept_id and spec_id:
            add_link(dept_id, spec_id, "OFFERS_SPECIALTY")
        if hosp_id and loc_id:
            add_link(hosp_id, loc_id, "LOCATED_IN")

    # Handle Multi-Hop Traversal Paths
    if graph_hop_info and graph_hop_info.get("graph_hopped"):
        origin_name = graph_hop_info.get("hop_origin")
        target_name = graph_hop_info.get("hop_target")
        traversal_path = graph_hop_info.get("traversal_path") or []
        step_distances = graph_hop_info.get("step_distances") or []

        if origin_name:
            origin_id = f"hosp_{origin_name}"
            add_node(origin_id, origin_name, "Hospital", "#f43f5e", 18, {
                "Role": "Origin Facility (Lacks Specialty)",
                "Hospital": origin_name
            })

        if len(traversal_path) >= 2:
            for i in range(len(traversal_path) - 1):
                n1 = traversal_path[i]
                n2 = traversal_path[i + 1]
                id1 = f"hosp_{n1}"
                id2 = f"hosp_{n2}"
                add_node(id1, n1, "Hospital", "#06b6d4", 15, {"Hospital": n1})
                add_node(id2, n2, "Hospital", "#06b6d4" if i + 1 < len(traversal_path) - 1 else "#10b981", 17, {"Hospital": n2})
                dist_km = step_distances[i] if i < len(step_distances) else None
                label = f"{dist_km} km" if dist_km else "CONNECTED_TO"
                add_link(id1, id2, "CONNECTED_TO", {
                    "distance_km": dist_km,
                    "highlighted": True,
                    "hop_step": i + 1,
                    "label": label
                })
        elif origin_name and target_name:
            origin_id = f"hosp_{origin_name}"
            target_id = f"hosp_{target_name}"
            add_link(origin_id, target_id, "CONNECTED_TO", {
                "distance_km": graph_hop_info.get("total_distance_km"),
                "highlighted": True
            })

    return {
        "nodes": nodes,
        "links": links,
        "node_count": len(nodes),
        "link_count": len(links)
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

    subgraph = build_subgraph_from_results(
        intent="doctor_disambiguation",
        results=results,
        disambiguation=True,
        candidates=cand_list
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
        "results": results,
        "subgraph": subgraph
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

    # Step 3: Return the graph results with interactive subgraph
    subgraph = build_subgraph_from_results(
        intent=intent,
        results=results,
        graph_hop_info=graph_hop_info,
        disambiguation=False
    )

    response_payload = {
        "question": question,
        "intent": intent,
        "count": len(results),
        "results": results,
        "subgraph": subgraph
    }
    if graph_hop_info:
        response_payload.update(graph_hop_info)

    return response_payload