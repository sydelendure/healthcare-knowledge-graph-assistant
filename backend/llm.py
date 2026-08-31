import os
import json
import re
import time

from dotenv import load_dotenv
from groq import Groq, RateLimitError, APIError


load_dotenv()

def _clean_env(val: str | None) -> str | None:
    if val is None:
        return None
    val = val.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    return val


api_key = _clean_env(os.getenv("GROQ_API_KEY"))

if not api_key:
    raise ValueError("GROQ_API_KEY is not set in environment or .env")

client = Groq(api_key=api_key)


SYSTEM_PROMPT = """
You are an intent extraction assistant for a healthcare knowledge graph.

The graph can answer questions about:
1. Finding doctors by medical specialty and district/city/hospital/region.
2. Finding doctors by medical specialty (when no location is specified).
3. Finding doctors working at a specific hospital.
4. Finding hospitals in a district or listing hospitals.
5. Finding information about a specific doctor by name (e.g. "tell me about dr arohi", "who is Dr Priya Kamat", "Dr Aarav Desai details", "info on doctor Kunal").

IMPORTANT CANONICAL SPECIALTY MAPPINGS:
The graph stores specialty names in their exact canonical form:
- general physician / general physicians / general practitioner / GP / family doctor / physician / general doctor -> General Medicine
- cardiologist / cardiologists / heart doctor -> Cardiology
- neurologist / neurologists / brain doctor -> Neurology
- dermatologist / dermatologists / skin doctor -> Dermatology
- pediatrician / pediatricians / child doctor / children doctor -> Pediatrics
- surgeon / surgeons / general surgeon -> General Surgery
- gynecologist / gynecologists / OBGYN -> Gynecology
- oncologist / oncologists / cancer doctor -> Oncology
- orthopedic / orthopedics / bone doctor -> Orthopedics
- urologist / urologists -> Urology
- nephrologist / nephrologists / kidney doctor -> Nephrology
- gastroenterologist / gastroenterologists / stomach doctor / GI -> Gastroenterology
- pulmonologist / pulmonologists / lung doctor -> Pulmonology
- ophthalmologist / ophthalmologists / eye doctor -> Ophthalmology
- ENT / otolaryngologist / ear nose throat -> Otolaryngology

IMPORTANT CANONICAL DISTRICT MAPPINGS:
- south / south district / southern / in south / south region -> South District
- north / north district / northern / in north / north region -> North District
- downtown / central / center / metro / downtown district -> Downtown

KNOWN CITIES / LOCALITIES:
- oak ridge / oakridge -> Oakridge
- metro center -> Metro Center
- northfield -> Northfield
- riverdale -> Riverdale
- bayview -> Bayview
- highland -> Highland
- pinecrest -> Pinecrest
- clearwater -> Clearwater
- silver spring -> Silver Spring
- fairview -> Fairview

INTENT FORMATS:

If the user specifies BOTH specialty and a location/city/hospital/district (e.g., "neurologist at oak ridge", "cardiologist in South District", "pediatrician in Bayview", "oncologist at Apex"):
{
    "intent": "doctors_by_specialty_and_location",
    "specialty": "canonical specialty name",
    "location": "location/city/district/hospital name"
}

If the user specifies specialty WITHOUT any location/district:
{
    "intent": "doctors_by_specialty",
    "specialty": "canonical specialty name"
}

If the user asks for all doctors at a specific hospital (without asking for a specialty, e.g. "doctors at Apex Advanced Med", "who works at Metro Central"):
{
    "intent": "doctors_by_hospital",
    "hospital_name": "hospital name"
}

If the user asks for hospitals in a district:
{
    "intent": "hospitals_by_district",
    "district": "canonical district name"
}

If the user asks to list all hospitals or show hospitals generally:
{
    "intent": "all_hospitals"
}

If the user mentions BOTH a specific doctor's name AND a specialty (e.g. "dr arohi neurology", "is dr arohi in neurology", "dr kunal cardiologist", "tell me if Dr Priya is a neurologist"):
{
    "intent": "doctor_and_specialty_check",
    "doctor_name": "Doctor name (e.g. Dr. Aarohi, Dr. Kunal, Dr. Priya)",
    "specialty": "canonical specialty name"
}

If the user asks about a specific doctor by name (e.g. "tell me about dr arohi", "who is Dr Priya Kamat", "Dr Aarav Desai details", "info on doctor Kunal"):
{
    "intent": "doctor_by_name",
    "doctor_name": "Doctor name (e.g. Dr. Aarohi, Priya Kamat, Aarav Desai)"
}

If the question cannot be answered using healthcare data:
{
    "intent": "unknown"
}

Return ONLY valid JSON.
"""

SPECIALTY_MAP = {
    "cardio": "Cardiology",
    "heart": "Cardiology",
    "derma": "Dermatology",
    "skin": "Dermatology",
    "endocrin": "Endocrinology",
    "gastro": "Gastroenterology",
    "stomach": "Gastroenterology",
    "general physician": "General Medicine",
    "general doctor": "General Medicine",
    "general medicine": "General Medicine",
    "gynecol": "Gynecology",
    "nephrol": "Nephrology",
    "kidney": "Nephrology",
    "neuro": "Neurology",
    "brain": "Neurology",
    "oncolog": "Oncology",
    "cancer": "Oncology",
    "ophthalm": "Ophthalmology",
    "eye": "Ophthalmology",
    "orthoped": "Orthopedics",
    "bone": "Orthopedics",
    "pediatric": "Pediatrics",
    "child": "Pediatrics",
    "psychiatr": "Psychiatry",
    "mental": "Psychiatry",
    "pulmonol": "Pulmonology",
    "lung": "Pulmonology",
    "urolog": "Urology"
}


def extract_specialty(text):
    t = text.lower()
    for k, v in SPECIALTY_MAP.items():
        if k in t:
            return v
    return None


def fast_rule_parser(q: str):
    q_low = q.lower().strip()

    doc_name = None
    m = re.search(r'\b(?:dr\.?|doctor)\s+([a-z]+(?:\s+[a-z]+)?)', q_low)
    if m:
        candidate = m.group(1).strip()
        non_names = {'located', 'in', 'at', 'working', 'who', 'specialist', 'specializing', 'for', 'near'}
        words = candidate.split()
        if words and words[0] not in non_names:
            # Strip trailing short connector words like 'a', 'an', 'is', 'in'
            clean_cand = re.sub(r'(?i)\b(a|an|is|in|at|for|the|of)\b$', '', candidate).strip()
            doc_name = f"Dr. {clean_cand.title()}" if clean_cand else m.group(0).title()

    spec = extract_specialty(q_low)

    if doc_name and spec:
        # Check if hospital name is also mentioned in the query
        hosp_match = None
        for h in ['apex advanced', 'apex regional', 'riverdale', 'metro central', 'north district', 'oakridge', 'bayview', 'highland', 'pinecrest', 'clearwater', 'silver spring', 'fairview', 'northfield']:
            if h in q_low:
                hosp_match = h.title()
                break

        clean_doc = re.sub(r'(?i)\b(neurology|cardiology|dermatology|orthopedics|pediatrics|general medicine|doctor|dr\.?)\b', '', doc_name).strip()
        clean_doc = re.sub(r'(?i)\b(a|an|is|in|at|for|the|of)\b$', '', clean_doc).strip()
        clean_doc = re.sub(r'^[.\s]+', '', clean_doc).strip()
        final_name = f"Dr. {clean_doc.title()}" if (clean_doc and not clean_doc.lower().startswith("dr")) else (clean_doc or doc_name)
        ret = {
            'intent': 'doctor_and_specialty_check',
            'doctor_name': final_name,
            'specialty': spec
        }
        if hosp_match:
            ret['hospital_name'] = hosp_match
        return ret

    if doc_name and ('who is' in q_low or 'tell me about' in q_low or 'details' in q_low or 'info' in q_low or len(q_low.split()) <= 4):
        clean_doc = re.sub(r'(?i)\b(who is|tell me about|details|info)\b', '', doc_name).strip()
        clean_doc = re.sub(r'^[.\s]+', '', clean_doc).strip()
        final_name = f"Dr. {clean_doc.title()}" if (clean_doc and not clean_doc.lower().startswith("dr")) else (clean_doc or doc_name)
        return {
            'intent': 'doctor_by_name',
            'doctor_name': final_name
        }

    # 2. Specialty with location / district / hospital
    if spec:
        dist = None
        if 'north' in q_low:
            dist = 'North District'
        elif 'south' in q_low:
            dist = 'South District'
        elif 'downtown' in q_low:
            dist = 'Downtown'

        loc = None
        for l in ['oak ridge', 'oakridge', 'bayview', 'highland', 'fairview', 'silver spring', 'metro central', 'north central', 'west end', 'riverdale', 'eastside']:
            if l in q_low:
                loc = l.title().replace('Oak Ridge', 'Oakridge')
                break

        if not loc and not dist:
            for prep in [' at ', ' in ', ' near ']:
                if prep in q_low:
                    raw_loc = q_low.split(prep)[-1].strip()
                    raw_loc = re.sub(r'\b(please|tell me|show me|find)\b', '', raw_loc).strip()
                    if raw_loc:
                        loc = raw_loc.title()
                        break

        if dist:
            return {
                'intent': 'doctors_by_specialty_and_district',
                'specialty': spec,
                'district': dist
            }
        if loc:
            return {
                'intent': 'doctors_by_specialty_and_location',
                'specialty': spec,
                'location': loc
            }
        return {
            'intent': 'doctors_by_specialty',
            'specialty': spec
        }

    # 3. Hospitals & non-specialty queries
    if 'hospital' in q_low or 'medical center' in q_low or 'facilities' in q_low:
        if ('doctor' in q_low or 'practitioner' in q_low or 'physician' in q_low or 'working' in q_low) and ' at ' in q_low:
            h_name = q_low.split(' at ')[-1].strip().title()
            return {'intent': 'doctors_by_hospital', 'hospital_name': h_name}
        if 'north' in q_low:
            return {'intent': 'hospitals_by_district', 'district': 'North District'}
        if 'south' in q_low:
            return {'intent': 'hospitals_by_district', 'district': 'South District'}
        if 'downtown' in q_low:
            return {'intent': 'hospitals_by_district', 'district': 'Downtown'}
        if 'all' in q_low or 'list' in q_low or 'show' in q_low:
            return {'intent': 'all_hospitals'}

    if any(w in q_low for w in ['capital of', 'weather in', 'recipe for', 'president of', 'who wrote', 'calculate']):
        return {'intent': 'unknown'}

    return None


def fuzzy_graph_fallback(question: str):
    q_low = question.lower().strip()

    # 1. Check specialties
    spec = extract_specialty(q_low)

    # 2. Check doctors
    doc_name = None
    m = re.search(r'\b(?:dr\.?|doctor)\s+([a-z]+(?:\s+[a-z]+)?)', q_low)
    if m:
        doc_name = m.group(0).title()

    if doc_name and spec:
        clean_doc = re.sub(r'(?i)\b(neurology|cardiology|dermatology|orthopedics|pediatrics|general medicine)\b', '', doc_name).strip()
        return {
            "intent": "doctor_and_specialty_check",
            "doctor_name": clean_doc or doc_name,
            "specialty": spec
        }
    if doc_name:
        return {
            "intent": "doctor_by_name",
            "doctor_name": doc_name
        }

    # 3. Check locations / districts
    dist = None
    if "north" in q_low:
        dist = "North District"
    elif "south" in q_low:
        dist = "South District"
    elif "downtown" in q_low:
        dist = "Downtown"

    loc = None
    for l in ["oak ridge", "oakridge", "bayview", "highland", "fairview", "silver spring", "metro central", "north central", "west end", "riverdale", "eastside"]:
        if l in q_low:
            loc = l.title().replace("Oak Ridge", "Oakridge")
            break

    if not loc and not dist:
        for prep in [' at ', ' in ', ' near ']:
            if prep in q_low:
                raw_loc = q_low.split(prep)[-1].strip()
                raw_loc = re.sub(r'\b(please|tell me|show me|find)\b', '', raw_loc).strip()
                if raw_loc:
                    loc = raw_loc.title()
                    break

    if spec and dist:
        return {"intent": "doctors_by_specialty_and_district", "specialty": spec, "district": dist}
    if spec and loc:
        return {"intent": "doctors_by_specialty_and_location", "specialty": spec, "location": loc}
    if spec:
        return {"intent": "doctors_by_specialty", "specialty": spec}

    # 4. Check hospitals
    if "hospital" in q_low or "medical center" in q_low:
        if dist:
            return {"intent": "hospitals_by_district", "district": dist}
        return {"intent": "all_hospitals"}

    if dist:
        return {"intent": "hospitals_by_district", "district": dist}

    return {"intent": "unknown"}


def understand_question(question: str):
    # 1. Check ultra-fast hybrid parser first
    fast_res = fast_rule_parser(question)
    if fast_res:
        return fast_res

    # 2. Query LLM with retry
    max_retries = 2
    delay = 1.0

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": question
                    }
                ],
                temperature=0
            )

            content = response.choices[0].message.content

            try:
                return json.loads(content)
            except json.JSONDecodeError:
                break

        except (RateLimitError, APIError, Exception) as e:
            if attempt < max_retries - 1 and ("429" in str(e) or "Rate limit" in str(e) or "busy" in str(e)):
                time.sleep(delay)
                delay *= 2
                continue
            # On rate limit or API failure, gracefully fall back to fuzzy extractor
            return fuzzy_graph_fallback(question)

    return fuzzy_graph_fallback(question)


if __name__ == "__main__":
    test_queries = [
        "neurologist at oak ridge",
        "cardiologists in South District",
        "Show doctors at Apex Advanced Medical Center",
        "find hospitals in north",
        "general physicians"
    ]
    for q in test_queries:
        print(f"Query: '{q}' -> {understand_question(q)}")