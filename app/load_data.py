import csv
import os
from app.database import Neo4jDatabase

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


def load_csv(filename):
    filepath = os.path.join(DATA_DIR, filename)
    with open(filepath, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def reload_database():
    db = Neo4jDatabase()
    
    locations = load_csv("locations.csv")
    hospitals = load_csv("hospitals.csv")
    specialties = load_csv("specialities.csv")
    doctors = load_csv("doctors.csv")

    with db.driver.session() as session:
        print("Clearing existing graph database...")
        session.run("MATCH (n) DETACH DELETE n")

        print(f"Creating {len(locations)} Location nodes...")
        for loc in locations:
            session.run("""
                CREATE (l:Location {
                    location_id: $location_id,
                    city: $city,
                    district: $district,
                    state: $state
                })
            """, location_id=loc["location_id"], city=loc["city"], district=loc["district"], state=loc["state"])

        print(f"Creating {len(specialties)} Speciality nodes...")
        for spec in specialties:
            session.run("""
                CREATE (s:Speciality {
                    specialty_id: $specialty_id,
                    name: $name
                })
            """, specialty_id=spec["specialty_id"], name=spec["name"])

        print(f"Creating {len(hospitals)} Hospital nodes & LOCATED_IN relationships...")
        for hosp in hospitals:
            session.run("""
                MATCH (l:Location {location_id: $location_id})
                CREATE (h:Hospital {
                    hospital_id: $hospital_id,
                    name: $name,
                    type: $type,
                    beds: toInteger($beds)
                })
                CREATE (h)-[:LOCATED_IN]->(l)
            """, hospital_id=hosp["hospital_id"], name=hosp["name"], type=hosp["type"], beds=hosp.get("beds", 0), location_id=hosp["location_id"])

        print(f"Creating {len(doctors)} Doctor nodes & relationships...")
        for doc in doctors:
            session.run("""
                MATCH (s:Speciality {specialty_id: $specialty_id})
                MATCH (h:Hospital {hospital_id: $hospital_id})
                CREATE (d:Doctor {
                    doctor_id: $doctor_id,
                    name: $name
                })
                CREATE (d)-[:HAS_SPECIALTY]->(s)
                CREATE (d)-[:WORKS_AT]->(h)
            """, doctor_id=doc["doctor_id"], name=doc["name"], specialty_id=doc["specialty_id"], hospital_id=doc["hospital_id"])

        print("Creating CONNECTED_TO relationships between hospitals with distance_km...")
        connections = load_csv("hospital_connections.csv")
        for conn in connections:
            session.run("""
                MATCH (h1:Hospital {hospital_id: $src})
                MATCH (h2:Hospital {hospital_id: $tgt})
                MERGE (h1)-[r1:CONNECTED_TO]->(h2)
                SET r1.distance_km = toFloat($dist)
                MERGE (h2)-[r2:CONNECTED_TO]->(h1)
                SET r2.distance_km = toFloat($dist)
            """, src=conn["source_hospital_id"], tgt=conn["target_hospital_id"], dist=conn.get("distance_km", 3.0))

        print("Creating Department nodes & relationships...")
        departments = load_csv("departments.csv")
        for dep in departments:
            session.run("""
                MATCH (h:Hospital {hospital_id: $hospital_id})
                CREATE (d:Department {
                    department_id: $department_id,
                    name: $name
                })
                CREATE (h)-[:HAS_DEPARTMENT]->(d)
                WITH d
                OPTIONAL MATCH (s:Speciality)
                WHERE toLower(s.name) = toLower($name)
                FOREACH (_ IN CASE WHEN s IS NOT NULL THEN [1] ELSE [] END |
                    CREATE (d)-[:OFFERS_SPECIALTY]->(s)
                )
            """, department_id=dep["department_id"], name=dep["name"], hospital_id=dep["hospital_id"])

        print("Creating Service nodes & relationships...")
        services = load_csv("services.csv")
        for svc in services:
            session.run("""
                CREATE (s:Service {
                    service_id: $service_id,
                    name: $name
                })
            """, service_id=svc["service_id"], name=svc["name"])

        # Link core hospital services based on hospital type / specialty
        session.run("""
            MATCH (h:Hospital), (s:Service)
            WHERE (s.name IN ['Emergency Care', 'Pharmacy', 'Ambulance Service', 'Blood Test', 'X-Ray'])
               OR (h.type = 'Private' AND s.name IN ['Health Checkup', 'Ultrasound', 'ECG'])
               OR (h.beds >= 300 AND s.name IN ['CT Scan', 'MRI', 'ICU', 'Dialysis'])
            MERGE (h)-[:OFFERS_SERVICE]->(s)
        """)

        # Verification count
        res = session.run("MATCH (n) RETURN labels(n) as label, count(*) as count").data()
        print("Updated Graph summary:", res)

    db.close()
    print("Database reload complete!")


if __name__ == "__main__":
    reload_database()
