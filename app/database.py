import os

from dotenv import load_dotenv
from neo4j import GraphDatabase


# Load variables from .env
load_dotenv()


def _clean_env(val: str | None) -> str | None:
    if val is None:
        return None
    val = val.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    return val


NEO4J_URI = _clean_env(os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL"))
NEO4J_USERNAME = _clean_env(os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER"))
NEO4J_PASSWORD = _clean_env(os.getenv("NEO4J_PASSWORD"))


class Neo4jDatabase:
    def __init__(self):
        if not NEO4J_URI or not NEO4J_USERNAME or not NEO4J_PASSWORD:
            missing = []
            if not NEO4J_URI:
                missing.append("NEO4J_URI")
            if not NEO4J_USERNAME:
                missing.append("NEO4J_USERNAME")
            if not NEO4J_PASSWORD:
                missing.append("NEO4J_PASSWORD")
            raise ValueError(f"Missing required Neo4j environment variable(s): {', '.join(missing)}")

        self.driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USERNAME, NEO4J_PASSWORD)
        )

    def close(self):
        self.driver.close()

    def verify_connection(self):
        try:
            self.driver.verify_connectivity()
            return True
        except Exception as e:
            print(f"Neo4j connection failed: {e}")
            return False


if __name__ == "__main__":
    db = Neo4jDatabase()

    if db.verify_connection():
        print("Successfully connected to Neo4j!")

    db.close()