from flask import Flask, render_template, request, jsonify
import requests
import os
from dotenv import load_dotenv

# Load variables from .env if present (local dev)
load_dotenv()

app = Flask(__name__)


def _clean_env(val: str | None, default: str = "") -> str:
    if val is None:
        return default
    val = val.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    return val or default


FASTAPI_URL = _clean_env(
    os.getenv("FASTAPI_URL"),
    "http://127.0.0.1:8001"
).rstrip("/")


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True)

    if not data or not isinstance(data, dict) or not data.get("question", "").strip():
        return jsonify({
            "error": "Please enter a question."
        }), 400

    question = data["question"].strip()

    try:
        response = requests.post(
            f"{FASTAPI_URL}/ask",
            json={"question": question},
            timeout=60
        )

        if response.status_code == 200:
            return jsonify(response.json())

        try:
            error_data = response.json()
            err_msg = error_data.get("detail") or error_data.get("error") or error_data.get("message")
            if not err_msg:
                err_msg = "The backend service was unable to process your question."
            return jsonify({"error": err_msg}), response.status_code
        except Exception:
            return jsonify({
                "error": "The backend service returned an unexpected response."
            }), response.status_code

    except requests.exceptions.ConnectionError:
        return jsonify({
            "error": (
                "Unable to connect to the healthcare backend service. "
                "If the backend is waking up, please try again in a few moments."
            )
        }), 503

    except requests.exceptions.Timeout:
        return jsonify({
            "error": (
                "The request to the healthcare backend timed out. "
                "Please try your question again."
            )
        }), 504

    except requests.exceptions.RequestException as e:
        app.logger.error("Frontend RequestException: %s", e)
        return jsonify({
            "error": "A network communication error occurred with the backend service."
        }), 502

    except Exception as e:
        app.logger.error("Frontend unexpected error: %s", e)
        return jsonify({
            "error": "An unexpected error occurred while processing your question."
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=False,
        threaded=True
    )