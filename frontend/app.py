from flask import Flask, render_template, request, jsonify
import requests
import os
from dotenv import load_dotenv

# Load variables from .env if present (local dev)
load_dotenv()

app = Flask(__name__)


def _clean_url(val: str | None, default: str = "http://127.0.0.1:8001") -> str:
    if val is None:
        return default
    val = val.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    if not val:
        return default
    if not val.startswith("http://") and not val.startswith("https://"):
        if "127.0.0.1" in val or "localhost" in val:
            val = f"http://{val}"
        else:
            val = f"https://{val}"
    return val.rstrip("/")


def get_fastapi_url() -> str:
    return _clean_url(os.getenv("FASTAPI_URL"))


FASTAPI_URL = get_fastapi_url()


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
    backend_url = get_fastapi_url()

    # If running on Render and FASTAPI_URL is still pointing to local default
    is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))
    if is_render and ("127.0.0.1" in backend_url or "localhost" in backend_url):
        return jsonify({
            "error": (
                "FASTAPI_URL environment variable is missing on Render. "
                "Please configure FASTAPI_URL=https://healthcare-knowledge-graph-assistant.onrender.com "
                "in your frontend service's Environment settings in Render."
            )
        }), 500

    try:
        response = requests.post(
            f"{backend_url}/ask",
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
            if response.status_code in [502, 503, 504]:
                return jsonify({
                    "error": "The backend service is currently waking up. Please try your question again in a few seconds."
                }), response.status_code
            return jsonify({
                "error": "The backend service returned an unexpected response. Please try again."
            }), response.status_code

    except (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema):
        return jsonify({
            "error": (
                f"Invalid FASTAPI_URL configured ('{backend_url}'). "
                "Please ensure it is a valid URL starting with https://"
            )
        }), 500

    except requests.exceptions.ConnectionError:
        return jsonify({
            "error": (
                "Unable to connect to the healthcare backend service. "
                "The backend service may be waking up (Render Free Tier). Please try again in a few moments."
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