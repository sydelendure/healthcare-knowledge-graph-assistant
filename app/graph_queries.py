from app.database import Neo4jDatabase


def get_doctors_by_specialty_and_district(specialty, district):
    query = """
    MATCH (d:Doctor)-[:WORKS_AT]->(h:Hospital)
          -[:LOCATED_IN]->(l:Location),
          (d)-[:HAS_SPECIALTY]->(s:Speciality)
    WHERE toLower(s.name) = toLower($specialty)
      AND (
        toLower(l.district) = toLower($district)
        OR toLower(l.district) CONTAINS toLower($district)
        OR toLower($district) CONTAINS toLower(l.district)
        OR toLower(l.city) = toLower($district)
      )
    RETURN
        d.doctor_id AS doctor_id,
        d.name AS doctor,
        s.name AS specialization,
        h.hospital_id AS hospital_id,
        h.name AS hospital,
        l.city AS city,
        l.district AS district
    ORDER BY d.name
    """

    db = Neo4jDatabase()

    try:
        with db.driver.session() as session:
            result = session.run(
                query,
                specialty=specialty,
                district=district
            )

            return [record.data() for record in result]

    finally:
        db.close()


def find_doctors_with_graph_hopping(specialty, location):
    """
    Intelligently traverses the Knowledge Graph:
    1. Direct match: Matches doctors in the exact city, facility, or district.
    2. Multi-Hop Graph Hopping: If 0 direct matches, traverses (Location/Hospital)->(District)->(Neighboring Facilities)->(Doctor).
    3. Network Expansion: If still 0, traverses across the entire health graph.
    """
    db = Neo4jDatabase()

    try:
        with db.driver.session() as session:
            # 1. Direct Search
            direct_query = """
            MATCH (d:Doctor)-[:WORKS_AT]->(h:Hospital)-[:LOCATED_IN]->(l:Location),
                  (d)-[:HAS_SPECIALTY]->(s:Speciality)
            WHERE toLower(s.name) = toLower($specialty)
              AND (
                toLower(l.city) CONTAINS toLower($location)
                OR toLower(l.district) CONTAINS toLower($location)
                OR toLower($location) CONTAINS toLower(l.city)
                OR toLower($location) CONTAINS toLower(l.district)
                OR toLower(h.name) CONTAINS toLower($location)
              )
            RETURN
                d.doctor_id AS doctor_id,
                d.name AS doctor,
                s.name AS specialization,
                h.hospital_id AS hospital_id,
                h.name AS hospital,
                l.city AS city,
                l.district AS district
            ORDER BY d.name
            """
            direct_results = [r.data() for r in session.run(direct_query, specialty=specialty, location=location)]

            if direct_results:
                return {
                    "results": direct_results,
                    "graph_hopped": False
                }

            # 2. Shortest Path Traversal via :CONNECTED_TO Hospital Network
            shortest_path_query = """
            MATCH (origin:Hospital)
            WHERE toLower(origin.name) CONTAINS toLower($location)
               OR toLower($location) CONTAINS toLower(origin.name)
               OR any(w IN split(toLower($location), " ") WHERE size(w) > 3 AND NOT w IN ["hospital", "center", "medical", "clinic"] AND toLower(origin.name) CONTAINS w)
            MATCH (target:Hospital)<-[:WORKS_AT]-(d:Doctor)-[:HAS_SPECIALTY]->(s:Speciality)
            WHERE toLower(s.name) = toLower($specialty) AND target <> origin
            MATCH p = shortestPath((origin)-[:CONNECTED_TO*1..5]-(target))
            OPTIONAL MATCH (target)-[:LOCATED_IN]->(l:Location)
            RETURN
                d.doctor_id AS doctor_id,
                d.name AS doctor,
                s.name AS specialization,
                target.hospital_id AS hospital_id,
                target.name AS hospital,
                l.city AS city,
                l.district AS district,
                length(p) AS hop_distance,
                round(reduce(total = 0.0, rel in relationships(p) | total + coalesce(rel.distance_km, 3.0)) * 10) / 10.0 AS total_distance_km,
                [rel in relationships(p) | coalesce(rel.distance_km, 3.0)] AS step_distances,
                [node in nodes(p) | node.name] AS traversal_path,
                origin.name AS origin_name,
                target.name AS target_name
            ORDER BY hop_distance ASC, total_distance_km ASC, d.name ASC
            """
            sp_results = [r.data() for r in session.run(shortest_path_query, specialty=specialty, location=location)]
            if sp_results:
                min_hop = sp_results[0]["hop_distance"]
                closest_results = [r for r in sp_results if r["hop_distance"] == min_hop]
                first_match = sp_results[0]
                return {
                    "results": closest_results,
                    "graph_hopped": True,
                    "hop_type": "shortest_path",
                    "hop_origin": first_match.get("origin_name") or location,
                    "hop_target": first_match.get("target_name"),
                    "hop_distance": min_hop,
                    "total_distance_km": first_match.get("total_distance_km"),
                    "step_distances": first_match.get("step_distances", []),
                    "traversal_path": first_match.get("traversal_path", [])
                }

            # 3. Multi-hop: Resolve location/hospital to its parent District
            loc_query = """
            MATCH (l:Location)
            WHERE toLower(l.city) CONTAINS toLower($location)
               OR toLower($location) CONTAINS toLower(l.city)
               OR toLower(l.district) CONTAINS toLower($location)
            RETURN DISTINCT l.district AS district LIMIT 1
            """
            loc_match = session.run(loc_query, location=location).single()

            if not loc_match:
                # Try finding district via hospital name
                hosp_loc_query = """
                MATCH (h:Hospital)-[:LOCATED_IN]->(l:Location)
                WHERE toLower(h.name) CONTAINS toLower($location)
                   OR toLower($location) CONTAINS toLower(h.name)
                RETURN DISTINCT l.district AS district LIMIT 1
                """
                loc_match = session.run(hosp_loc_query, location=location).single()

            if loc_match and loc_match.get("district"):
                district = loc_match["district"]
                district_query = """
                MATCH (d:Doctor)-[:WORKS_AT]->(h:Hospital)-[:LOCATED_IN]->(l:Location),
                      (d)-[:HAS_SPECIALTY]->(s:Speciality)
                WHERE toLower(s.name) = toLower($specialty)
                  AND toLower(l.district) = toLower($district)
                RETURN
                    d.doctor_id AS doctor_id,
                    d.name AS doctor,
                    s.name AS specialization,
                    h.hospital_id AS hospital_id,
                    h.name AS hospital,
                    l.city AS city,
                    l.district AS district
                ORDER BY d.name
                """
                district_results = [r.data() for r in session.run(district_query, specialty=specialty, district=district)]

                if district_results:
                    return {
                        "results": district_results,
                        "graph_hopped": True,
                        "hop_type": "district",
                        "hop_origin": location,
                        "hop_target": district
                    }

            # 4. Network-wide graph fallback
            all_spec_query = """
            MATCH (d:Doctor)-[:WORKS_AT]->(h:Hospital)-[:LOCATED_IN]->(l:Location),
                  (d)-[:HAS_SPECIALTY]->(s:Speciality)
            WHERE toLower(s.name) = toLower($specialty)
            RETURN
                d.doctor_id AS doctor_id,
                d.name AS doctor,
                s.name AS specialization,
                h.hospital_id AS hospital_id,
                h.name AS hospital,
                l.city AS city,
                l.district AS district
            ORDER BY d.name
            """
            all_results = [r.data() for r in session.run(all_spec_query, specialty=specialty)]

            if all_results:
                districts_found = sorted(list(set(r["district"] for r in all_results if r.get("district"))))
                target_label = " & ".join(districts_found) if districts_found else "Connected Districts"
                return {
                    "results": all_results,
                    "graph_hopped": True,
                    "hop_type": "network",
                    "hop_origin": location,
                    "hop_target": target_label
                }

            return {
                "results": [],
                "graph_hopped": False
            }

    finally:
        db.close()


def get_doctors_by_specialty(specialty):
    query = """
    MATCH (d:Doctor)-[:WORKS_AT]->(h:Hospital)
          -[:LOCATED_IN]->(l:Location),
          (d)-[:HAS_SPECIALTY]->(s:Speciality)
    WHERE toLower(s.name) = toLower($specialty)
    RETURN
        d.doctor_id AS doctor_id,
        d.name AS doctor,
        s.name AS specialization,
        h.hospital_id AS hospital_id,
        h.name AS hospital,
        l.city AS city,
        l.district AS district
    ORDER BY d.name
    """

    db = Neo4jDatabase()

    try:
        with db.driver.session() as session:
            result = session.run(
                query,
                specialty=specialty
            )

            return [record.data() for record in result]

    finally:
        db.close()


def get_doctors_by_hospital(hospital_name):
    query = """
    MATCH (d:Doctor)-[:WORKS_AT]->(h:Hospital)
    WHERE toLower(h.name) = toLower($hospital_name)
       OR toLower(h.name) CONTAINS toLower($hospital_name)
    OPTIONAL MATCH (d)-[:HAS_SPECIALTY]->(s:Speciality)
    OPTIONAL MATCH (h)-[:LOCATED_IN]->(l:Location)
    RETURN
        d.doctor_id AS doctor_id,
        d.name AS doctor,
        s.name AS specialization,
        h.hospital_id AS hospital_id,
        h.name AS hospital,
        l.city AS city,
        l.district AS district
    ORDER BY d.name
    """

    db = Neo4jDatabase()

    try:
        with db.driver.session() as session:
            result = session.run(
                query,
                hospital_name=hospital_name
            )

            return [record.data() for record in result]

    finally:
        db.close()


def get_hospitals_by_district(district):
    query = """
    MATCH (h:Hospital)-[:LOCATED_IN]->(l:Location)
    WHERE toLower(l.district) = toLower($district)
       OR toLower(l.district) CONTAINS toLower($district)
       OR toLower($district) CONTAINS toLower(l.district)
       OR toLower(l.city) = toLower($district)
    RETURN
        h.hospital_id AS hospital_id,
        h.name AS hospital,
        h.type AS hospital_type,
        h.beds AS beds,
        l.city AS city,
        l.district AS district
    ORDER BY h.name
    """

    db = Neo4jDatabase()

    try:
        with db.driver.session() as session:
            result = session.run(
                query,
                district=district
            )

            return [record.data() for record in result]

    finally:
        db.close()


def get_all_hospitals():
    query = """
    MATCH (h:Hospital)-[:LOCATED_IN]->(l:Location)
    RETURN
        h.hospital_id AS hospital_id,
        h.name AS hospital,
        h.type AS hospital_type,
        h.beds AS beds,
        l.city AS city,
        l.district AS district
    ORDER BY h.name
    """

    db = Neo4jDatabase()

    try:
        with db.driver.session() as session:
            result = session.run(query)
            return [record.data() for record in result]

    finally:
        db.close()


def get_doctor_by_name(doctor_name):
    clean_name = doctor_name.strip()
    for prefix in ["dr.", "doctor ", "dr ", "dr"]:
        if clean_name.lower().startswith(prefix):
            clean_name = clean_name[len(prefix):].strip()
            break

    query = """
    MATCH (d:Doctor)
    WHERE toLower(d.name) CONTAINS toLower($name)
       OR toLower(d.name) CONTAINS toLower($clean_name)
       OR toLower($name) CONTAINS toLower(d.name)
       OR toLower($clean_name) CONTAINS toLower(d.name)
    OPTIONAL MATCH (d)-[:HAS_SPECIALTY]->(s:Speciality)
    OPTIONAL MATCH (d)-[:WORKS_AT]->(h:Hospital)-[:LOCATED_IN]->(l:Location)
    RETURN
        d.doctor_id AS doctor_id,
        d.name AS doctor,
        s.name AS specialization,
        h.hospital_id AS hospital_id,
        h.name AS hospital,
        l.city AS city,
        l.district AS district
    ORDER BY d.name
    """

    db = Neo4jDatabase()
    try:
        with db.driver.session() as session:
            result = session.run(query, name=doctor_name, clean_name=clean_name)
            return [record.data() for record in result]
    finally:
        db.close()


def check_doctor_specialty_with_graph_hopping(doctor_name, requested_specialty):
    clean_name = doctor_name.strip()
    for prefix in ["dr.", "doctor ", "dr ", "dr"]:
        if clean_name.lower().startswith(prefix):
            clean_name = clean_name[len(prefix):].strip()
            break

    db = Neo4jDatabase()
    try:
        with db.driver.session() as session:
            # 1. Fetch doctor node
            doc_query = """
            MATCH (d:Doctor)
            WHERE toLower(d.name) CONTAINS toLower($name)
               OR toLower(d.name) CONTAINS toLower($clean_name)
               OR toLower($name) CONTAINS toLower(d.name)
               OR toLower($clean_name) CONTAINS toLower(d.name)
            OPTIONAL MATCH (d)-[:HAS_SPECIALTY]->(s:Speciality)
            OPTIONAL MATCH (d)-[:WORKS_AT]->(h:Hospital)-[:LOCATED_IN]->(l:Location)
            RETURN
                d.doctor_id AS doctor_id,
                d.name AS doctor,
                s.name AS actual_specialty,
                h.hospital_id AS hospital_id,
                h.name AS hospital,
                l.city AS city,
                l.district AS district
            LIMIT 1
            """
            doc_record = session.run(doc_query, name=doctor_name, clean_name=clean_name).single()

            if not doc_record:
                return find_doctors_with_graph_hopping(requested_specialty, "")

            doc_data = dict(doc_record)
            actual_specialty = doc_data.get("actual_specialty") or "General Medicine"
            doc_name = doc_data.get("doctor") or doctor_name
            hospital = doc_data.get("hospital") or ""
            district = doc_data.get("district") or ""

            # Check if doctor matches requested specialty
            if actual_specialty.lower() == requested_specialty.lower():
                return {
                    "results": [{
                        "doctor_id": doc_data.get("doctor_id"),
                        "doctor": doc_name,
                        "specialization": actual_specialty,
                        "hospital_id": doc_data.get("hospital_id"),
                        "hospital": hospital,
                        "city": doc_data.get("city"),
                        "district": district
                    }],
                    "is_specialty_match": True,
                    "doctor_name": doc_name,
                    "actual_specialty": actual_specialty,
                    "requested_specialty": requested_specialty,
                    "graph_hopped": False
                }

            # Doctor does NOT match requested specialty!
            # Perform multi-hop graph traversal to find requested specialists at same hospital / district
            hop_query = """
            MATCH (d:Doctor)-[:WORKS_AT]->(h:Hospital)-[:LOCATED_IN]->(l:Location),
                  (d)-[:HAS_SPECIALTY]->(s:Speciality)
            WHERE toLower(s.name) = toLower($requested_specialty)
              AND (
                toLower(l.district) = toLower($district)
                OR toLower(h.name) = toLower($hospital)
              )
            RETURN
                d.doctor_id AS doctor_id,
                d.name AS doctor,
                s.name AS specialization,
                h.hospital_id AS hospital_id,
                h.name AS hospital,
                l.city AS city,
                l.district AS district
            ORDER BY CASE WHEN toLower(h.name) = toLower($hospital) THEN 0 ELSE 1 END, d.name
            """
            hop_results = [r.data() for r in session.run(hop_query, requested_specialty=requested_specialty, district=district, hospital=hospital)]

            if not hop_results:
                all_res = find_doctors_with_graph_hopping(requested_specialty, "")
                hop_results = all_res.get("results", [])

            return {
                "results": hop_results,
                "is_specialty_match": False,
                "doctor_name": doc_name,
                "actual_specialty": actual_specialty,
                "requested_specialty": requested_specialty,
                "hospital": hospital,
                "district": district,
                "graph_hopped": True,
                "hop_type": "specialty_mismatch",
                "hop_origin": f"{doc_name} ({actual_specialty})",
                "hop_target": f"{hospital} & {district}" if (hospital and district) else (district or "Regional Network")
            }

    finally:
        db.close()


def find_closest_hospitals(origin_hospital, specialty=None):
    """
    Finds the closest hospital(s) in the referral network using shortestPath algorithm.
    """
    db = Neo4jDatabase()
    try:
        with db.driver.session() as session:
            if specialty:
                query = """
                MATCH (origin:Hospital)
                WHERE toLower(origin.name) CONTAINS toLower($origin_hospital)
                   OR toLower($origin_hospital) CONTAINS toLower(origin.name)
                   OR any(w IN split(toLower($origin_hospital), " ") WHERE size(w) > 3 AND NOT w IN ["hospital", "center", "medical", "clinic"] AND toLower(origin.name) CONTAINS w)
                MATCH (target:Hospital)<-[:WORKS_AT]-(d:Doctor)-[:HAS_SPECIALTY]->(s:Speciality)
                WHERE toLower(s.name) = toLower($specialty) AND target <> origin
                MATCH p = shortestPath((origin)-[:CONNECTED_TO*1..5]-(target))
                OPTIONAL MATCH (target)-[:LOCATED_IN]->(l:Location)
                RETURN
                    target.hospital_id AS hospital_id,
                    target.name AS hospital,
                    target.type AS hospital_type,
                    l.city AS city,
                    l.district AS district,
                    length(p) AS hop_distance,
                    round(reduce(total = 0.0, rel in relationships(p) | total + coalesce(rel.distance_km, 3.0)) * 10) / 10.0 AS total_distance_km,
                    [rel in relationships(p) | coalesce(rel.distance_km, 3.0)] AS step_distances,
                    [node in nodes(p) | node.name] AS traversal_path,
                    collect(d.name) AS doctors
                ORDER BY hop_distance ASC, total_distance_km ASC
                """
                results = [r.data() for r in session.run(query, origin_hospital=origin_hospital, specialty=specialty)]
            else:
                query = """
                MATCH (origin:Hospital)
                WHERE toLower(origin.name) CONTAINS toLower($origin_hospital)
                   OR toLower($origin_hospital) CONTAINS toLower(origin.name)
                   OR any(w IN split(toLower($origin_hospital), " ") WHERE size(w) > 3 AND NOT w IN ["hospital", "center", "medical", "clinic"] AND toLower(origin.name) CONTAINS w)
                MATCH (target:Hospital)
                WHERE target <> origin
                MATCH p = shortestPath((origin)-[:CONNECTED_TO*1..5]-(target))
                OPTIONAL MATCH (target)-[:LOCATED_IN]->(l:Location)
                RETURN
                    target.hospital_id AS hospital_id,
                    target.name AS hospital,
                    target.type AS hospital_type,
                    l.city AS city,
                    l.district AS district,
                    length(p) AS hop_distance,
                    round(reduce(total = 0.0, rel in relationships(p) | total + coalesce(rel.distance_km, 3.0)) * 10) / 10.0 AS total_distance_km,
                    [rel in relationships(p) | coalesce(rel.distance_km, 3.0)] AS step_distances,
                    [node in nodes(p) | node.name] AS traversal_path
                ORDER BY hop_distance ASC, total_distance_km ASC
                """
                results = [r.data() for r in session.run(query, origin_hospital=origin_hospital)]

            return results
    finally:
        db.close()