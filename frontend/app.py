from flask import Flask, render_template, request, jsonify
import requests

app = Flask(__name__)

FASTAPI_URL = "http://127.0.0.1:8001"


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()

    if not data or not data.get("question", "").strip():
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
                err_msg = "Something went wrong while processing your question."
            return jsonify({"error": err_msg}), response.status_code
        except Exception:
            return jsonify({
                "error": response.text or "The backend returned an unexpected response."
            }), response.status_code

    except requests.exceptions.ConnectionError:
        return jsonify({
            "error": (
                "The healthcare backend is not running. "
                "Please make sure FastAPI is running on port 8001."
            )
        }), 503

    except requests.exceptions.Timeout:
        return jsonify({
            "error": (
                "The request took too long. "
                "Please try your question again."
            )
        }), 504

    except Exception as e:
        print("Frontend error:", e)

        return jsonify({
            "error": "An unexpected error occurred."
        }), 500


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
        threaded=True
    )