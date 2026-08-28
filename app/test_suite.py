import time
import requests
import json
import concurrent.futures
from app.database import Neo4jDatabase
from app.graph_queries import (
    get_doctors_by_specialty_and_district,
    get_doctors_by_specialty,
    get_doctors_by_hospital,
    get_hospitals_by_district,
    get_all_hospitals,
)
from backend.llm import understand_question

FASTAPI_URL = "http://127.0.0.1:8001"
FLASK_URL = "http://127.0.0.1:5000"

PASSED = 0
FAILED = 0
TOTAL = 0


def record_result(test_name, success, details=""):
    global PASSED, FAILED, TOTAL
    TOTAL += 1
    if success:
        PASSED += 1
        print(f"  [PASS] {test_name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {test_name} --> {details}")


def section(title):
    print("\n" + "=" * 60)
    print(f"  {title.upper()}")
    print("=" * 60)


# =========================================================================
# TEST SUITE 1: NEO4J GRAPH DATABASE INTEGRITY
# =========================================================================
def test_database_integrity():
    section("1. Neo4j Graph Database Integrity & Consistency")
    db = Neo4jDatabase()
    
    with db.driver.session() as session:
        # 1.1 Node counts
        nodes = session.run("MATCH (n) RETURN labels(n)[0] as label, count(*) as count").data()
        node_counts = {item['label']: item['count'] for item in nodes}
        
        record_result("Location node count == 10", node_counts.get("Location") == 10, f"Got: {node_counts.get('Location')}")
        record_result("Speciality node count == 15", node_counts.get("Speciality") == 15, f"Got: {node_counts.get('Speciality')}")
        record_result("Hospital node count == 20", node_counts.get("Hospital") == 20, f"Got: {node_counts.get('Hospital')}")
        record_result("Doctor node count == 50", node_counts.get("Doctor") == 50, f"Got: {node_counts.get('Doctor')}")

        # 1.2 Relationship integrity
        orphan_docs = session.run("MATCH (d:Doctor) WHERE NOT (d)-[:WORKS_AT]->(:Hospital) OR NOT (d)-[:HAS_SPECIALTY]->(:Speciality) RETURN count(d) as count").single()["count"]
        record_result("Zero orphan doctors (all have hospital & specialty)", orphan_docs == 0, f"Found {orphan_docs} orphans")

        orphan_hosps = session.run("MATCH (h:Hospital) WHERE NOT (h)-[:LOCATED_IN]->(:Location) RETURN count(h) as count").single()["count"]
        record_result("Zero orphan hospitals (all have location)", orphan_hosps == 0, f"Found {orphan_hosps} orphans")

        # 1.3 Property presence
        missing_doc_props = session.run("MATCH (d:Doctor) WHERE d.name IS NULL OR d.doctor_id IS NULL RETURN count(d) as count").single()["count"]
        record_result("All doctors have valid name & id", missing_doc_props == 0, f"Found {missing_doc_props} invalid")

        missing_hosp_props = session.run("MATCH (h:Hospital) WHERE h.name IS NULL OR h.hospital_id IS NULL OR h.type IS NULL RETURN count(h) as count").single()["count"]
        record_result("All hospitals have name, id, and type", missing_hosp_props == 0, f"Found {missing_hosp_props} invalid")

    db.close()


# =========================================================================
# TEST SUITE 2: GRAPH QUERY PYTHON LAYER
# =========================================================================
def test_graph_queries():
    section("2. Direct Graph Query Function Tests")
    
    # 2.1 Doctors by specialty and district
    res1 = get_doctors_by_specialty_and_district("Cardiology", "South District")
    record_result("Query Cardiology in South District returns 2 doctors", len(res1) == 2, f"Got: {len(res1)}")
    
    res1_case = get_doctors_by_specialty_and_district("cArDiOlOgY", "sOuTh dIsTrIcT")
    record_result("Query is case-insensitive for specialty & district", len(res1_case) == 2, f"Got: {len(res1_case)}")
    
    # 2.2 Doctors by specialty only
    res_spec = get_doctors_by_specialty("General Medicine")
    record_result("Query General Medicine returns all 3 doctors", len(res_spec) == 3, f"Got: {len(res_spec)}")

    # 2.3 Doctors by hospital
    res2 = get_doctors_by_hospital("Apex Advanced Medical Center")
    record_result("Query doctors at Apex Advanced Medical Center returns 5 doctors", len(res2) == 5, f"Got: {len(res2)}")
    record_result("Doctors by hospital have valid specialization & location", bool(res2[0].get("specialization") and res2[0].get("city")), f"Got: {res2[0]}")

    # 2.4 Hospitals by district
    res3 = get_hospitals_by_district("North District")
    record_result("Query hospitals in North District returns 8 hospitals", len(res3) == 8, f"Got: {len(res3)}")

    res4 = get_hospitals_by_district("South District")
    record_result("Query hospitals in South District returns 10 hospitals", len(res4) == 10, f"Got: {len(res4)}")


# =========================================================================
# TEST SUITE 3: LLM INTENT EXTRACTION
# =========================================================================
def test_llm_intent_extraction():
    section("3. LLM Natural Language Intent & Entity Extraction")

    test_cases = [
        {
            "query": "general physicians",
            "expected_intent": "doctors_by_specialty",
            "expected_specialty": "General Medicine"
        },
        {
            "query": "Find cardiologists in South District",
            "expected_intent": "doctors_by_specialty_and_district",
            "expected_specialty": "Cardiology",
            "expected_district": "South District"
        },
        {
            "query": "I need a heart doctor located in South District",
            "expected_intent": "doctors_by_specialty_and_district",
            "expected_specialty": "Cardiology",
            "expected_district": "South District"
        },
        {
            "query": "Show neurologists in North District",
            "expected_intent": "doctors_by_specialty_and_district",
            "expected_specialty": "Neurology",
            "expected_district": "North District"
        },
        {
            "query": "Show doctors working at Metro Central Medical Center",
            "expected_intent": "doctors_by_hospital",
            "expected_hospital": "Metro Central Medical Center"
        },
        {
            "query": "List all hospitals in North District",
            "expected_intent": "hospitals_by_district",
            "expected_district": "North District"
        },
        {
            "query": "neurologist at oak ridge",
            "expected_intent": "doctors_by_specialty_and_location",
            "expected_specialty": "Neurology"
        },
        {
            "query": "tell me about dr arohi",
            "expected_intent": "doctor_by_name"
        },
        {
            "query": "dr arohi neurology",
            "expected_intent": "doctor_and_specialty_check"
        },
        {
            "query": "who is Dr Priya Kamat",
            "expected_intent": "doctor_by_name"
        },
        {
            "query": "What is the capital of France?",
            "expected_intent": "unknown"
        }
    ]

    for tc in test_cases:
        try:
            res = understand_question(tc["query"])
            intent = res.get("intent")
            match = (intent == tc["expected_intent"] or (tc["expected_intent"] == "doctors_by_specialty_and_district" and intent == "doctors_by_specialty_and_location"))
            if match and "expected_specialty" in tc:
                match = (res.get("specialty") == tc["expected_specialty"])
            if match and "expected_district" in tc:
                match = (res.get("district") == tc["expected_district"] or res.get("location") == tc["expected_district"])
            if match and "expected_hospital" in tc:
                match = (res.get("hospital_name") == tc["expected_hospital"])
            
            record_result(f"LLM Intent for: '{tc['query']}'", match, f"Output: {res}")
            time.sleep(0.3)
        except Exception as e:
            record_result(f"LLM Intent for: '{tc['query']}'", False, f"Exception: {e}")


# =========================================================================
# TEST SUITE 4: FASTAPI BACKEND SERVICE (PORT 8001)
# =========================================================================
def test_fastapi_endpoints():
    section("4. FastAPI Service Endpoints (Port 8001)")

    # 4.1 Health Check
    r = requests.get(f"{FASTAPI_URL}/")
    record_result("GET / returns 200 OK", r.status_code == 200, f"Status: {r.status_code}")

    # 4.2 Structured endpoints
    r = requests.get(f"{FASTAPI_URL}/doctors", params={"specialty": "Cardiology", "district": "South District"})
    record_result("GET /doctors returns 200 with results", r.status_code == 200 and r.json().get("count") == 2, f"Body: {r.text}")

    r = requests.get(f"{FASTAPI_URL}/doctors/hospital", params={"hospital_name": "Apex Advanced Medical Center"})
    record_result("GET /doctors/hospital returns 200 with 5 doctors", r.status_code == 200 and r.json().get("count") == 5, f"Body: {r.text}")

    r = requests.get(f"{FASTAPI_URL}/hospitals", params={"district": "South District"})
    record_result("GET /hospitals returns 200 with 10 hospitals", r.status_code == 200 and r.json().get("count") == 10, f"Body: {r.text}")

    # 4.3 POST /ask
    payload = {"question": "Find pediatricians in South District"}
    r = requests.post(f"{FASTAPI_URL}/ask", json=payload)
    record_result("POST /ask valid question returns 200", r.status_code == 200 and "results" in r.json(), f"Body: {r.text}")

    # 4.4 Out-of-scope question handling
    payload = {"question": "Tell me a joke about robots"}
    r = requests.post(f"{FASTAPI_URL}/ask", json=payload)
    record_result("POST /ask out-of-scope question returns helpful answer message", r.status_code == 200 and "answer" in r.json(), f"Body: {r.text}")

    # 4.5 Empty question validation
    r = requests.post(f"{FASTAPI_URL}/ask", json={"question": "   "})
    record_result("POST /ask empty question returns 400 Bad Request", r.status_code == 400, f"Status: {r.status_code}")


# =========================================================================
# TEST SUITE 5: FLASK FRONTEND PROXY & ASSETS (PORT 5000)
# =========================================================================
def test_flask_frontend():
    section("5. Flask Frontend Service & Proxy (Port 5000)")

    # 5.1 HTML Template
    r = requests.get(f"{FLASK_URL}/")
    record_result("GET / returns 200 OK", r.status_code == 200, f"Status: {r.status_code}")
    record_result("HTML contains 'HEALTHGRAPH'", "HEALTHGRAPH" in r.text, "Branding missing in HTML")
    record_result("HTML contains New Search button", "New Search" in r.text, "New search missing in HTML")
    record_result("HTML contains chat input textarea", "id=\"welcomeQuestionInput\"" in r.text, "Textarea missing in HTML")

    # 5.2 CSS Assets
    r_css = requests.get(f"{FLASK_URL}/static/style.css")
    record_result("GET /static/style.css returns 200 OK", r_css.status_code == 200, f"Status: {r_css.status_code}")
    record_result("CSS contains --primary-green token", "--primary-green" in r_css.text, "Design token missing")

    # 5.3 Proxy POST /ask
    payload = {"question": "Find cardiologists in South District"}
    r_ask = requests.post(f"{FLASK_URL}/ask", json=payload)
    record_result("POST /ask through Flask proxies to FastAPI and returns 200", r_ask.status_code == 200 and r_ask.json().get("count") == 2, f"Body: {r_ask.text}")

    # 5.4 Proxy Empty validation
    r_empty = requests.post(f"{FLASK_URL}/ask", json={"question": ""})
    record_result("POST /ask with empty string returns 400 from Flask", r_empty.status_code == 400, f"Status: {r_empty.status_code}")


# =========================================================================
# TEST SUITE 6: SECURITY & INJECTION VULNERABILITY TESTS
# =========================================================================
def test_security_and_edge_cases():
    section("6. Security, Injection & Edge Cases")

    malicious_inputs = [
        "' OR 1=1 --",
        "MATCH (n) DETACH DELETE n",
        "<script>alert('xss')</script>",
        "'; DROP TABLE users; --",
        "A" * 1000,
        "../../../../etc/passwd"
    ]

    for mal in malicious_inputs:
        r = requests.post(f"{FLASK_URL}/ask", json={"question": mal})
        safe = r.status_code in [200, 400, 429]
        record_result(f"Safe handling of input: {mal[:28]}...", safe, f"Status {r.status_code}: {r.text[:100]}")
        time.sleep(0.3)


# =========================================================================
# TEST SUITE 7: CONCURRENCY & LOAD PERFORMANCE
# =========================================================================
def test_concurrency_and_performance():
    section("7. Concurrency & Load Performance")

    queries = [
        "Find cardiologists in South District",
        "Find neurologists in North District",
        "Show doctors at Apex Advanced Medical Center",
        "Find hospitals in South District",
        "general physicians"
    ]

    start_time = time.time()
    
    def send_req(q):
        for attempt in range(3):
            try:
                resp = requests.post(f"{FLASK_URL}/ask", json={"question": q}, timeout=30)
                if resp.status_code == 200:
                    return True
                elif resp.status_code in (429, 503, 504):
                    time.sleep(2.0 * (attempt + 1))
                    continue
            except Exception:
                time.sleep(2.0 * (attempt + 1))
                continue
        return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(send_req, queries))

    elapsed = time.time() - start_time
    success_rate = sum(results) / len(results) * 100

    record_result(f"Concurrent load (5 queries) completed in {elapsed:.2f}s (100% success)", success_rate == 100, f"Success rate: {success_rate}%")


# =========================================================================
# MAIN EXECUTION
# =========================================================================
if __name__ == "__main__":
    print("\n" + "#" * 60)
    print("  COMMENCING RIGOROUS AUTOMATED TEST SUITE")
    print("#" * 60)

    t0 = time.time()
    test_database_integrity()
    test_graph_queries()
    test_llm_intent_extraction()
    test_fastapi_endpoints()
    test_flask_frontend()
    test_security_and_edge_cases()
    test_concurrency_and_performance()
    t_total = time.time() - t0

    print("\n" + "=" * 60)
    print(f"  TEST RESULTS SUMMARY")
    print("=" * 60)
    print(f"  Total Tests Executed: {TOTAL}")
    print(f"  Passed: {PASSED}")
    print(f"  Failed: {FAILED}")
    print(f"  Success Rate: {(PASSED/TOTAL)*100:.1f}%")
    print(f"  Total Duration: {t_total:.2f} seconds")
    print("=" * 60 + "\n")
