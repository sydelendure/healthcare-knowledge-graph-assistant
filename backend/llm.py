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

HOSPITAL_PATTERNS = [
    # Full canonical names
    (r'\bmetro central medical center\b', 'Metro Central Medical Center'),
    (r'\bminesotta specialty hospital\b', 'Minesotta Specialty Hospital'),
    (r'\briverdale general hospital\b', 'Riverdale General Hospital'),
    (r'\bbayview healthcare center\b', 'Bayview Healthcare Center'),
    (r'\bbridge candy medical center\b', 'Bridge Candy Medical Center'),
    (r'\boakridge specialty hospital\b', 'Oakridge Specialty Hospital'),
    (r'\bhighland community hospital\b', 'Highland Community Hospital'),
    (r'\bpinecrest medical institute\b', 'Pinecrest Medical Institute'),
    (r'\bclearwater general hospital\b', 'Clearwater General Hospital'),
    (r'\bsilver spring healthcare center\b', 'Silver Spring Healthcare Center'),
    (r'\bfairview medical center\b', 'Fairview Medical Center'),
    (r'\bapex regional hospital\b', 'Apex Regional Hospital'),
    (r'\bnorthfield community hospital\b', 'Northfield Community Hospital'),
    (r'\briverdale specialty center\b', 'Riverdale Specialty Center'),
    (r'\bbayview general hospital\b', 'Bayview General Hospital'),
    (r'\boakridge general hospital\b', 'Oakridge General Hospital'),
    (r'\bpinecrest community hospital\b', 'Pinecrest Community Hospital'),
    (r'\bclearwater specialty center\b', 'Clearwater Specialty Center'),
    (r'\bsilver spring general hospital\b', 'Silver Spring General Hospital'),
    (r'\bapex advanced medical center\b', 'Apex Advanced Medical Center'),
    # Recognizable keywords/prefixes
    (r'\bapex advanced\b', 'Apex Advanced Medical Center'),
    (r'\bapex regional\b', 'Apex Regional Hospital'),
    (r'\bbridge candy\b', 'Bridge Candy Medical Center'),
    (r'\bmetro central\b', 'Metro Central Medical Center'),
    (r'\bminesotta\b', 'Minesotta Specialty Hospital'),
    (r'\bnorthfield community\b', 'Northfield Community Hospital'),
    (r'\bnorthfield\b', 'Northfield Community Hospital'),
    (r'\bhighland community\b', 'Highland Community Hospital'),
    (r'\bfairview\b', 'Fairview Medical Center'),
    (r'\briverdale general\b', 'Riverdale General Hospital'),
    (r'\briverdale specialty\b', 'Riverdale Specialty Center'),
    (r'\bbayview general\b', 'Bayview General Hospital'),
    (r'\bbayview healthcare\b', 'Bayview Healthcare Center'),
    (r'\boakridge general\b', 'Oakridge General Hospital'),
    (r'\boakridge specialty\b', 'Oakridge Specialty Hospital'),
    (r'\bpinecrest medical\b', 'Pinecrest Medical Institute'),
    (r'\bpinecrest community\b', 'Pinecrest Community Hospital'),
    (r'\bclearwater general\b', 'Clearwater General Hospital'),
    (r'\bclearwater specialty\b', 'Clearwater Specialty Center'),
    (r'\bsilver spring general\b', 'Silver Spring General Hospital'),
    (r'\bsilver spring healthcare\b', 'Silver Spring Healthcare Center'),
]


def extract_specialty(text: str):
    t = text.lower()
    for k, v in SPECIALTY_MAP.items():
        if k in t:
            return v
    return None


def extract_hospital(text: str):
    t = text.lower()
    for pattern, name in HOSPITAL_PATTERNS:
        if re.search(pattern, t):
            return name
    for prep in [' at ', ' in ', ' for ']:
        if prep in t:
            after = t.split(prep)[-1].strip()
            if any(w in after for w in ['hospital', 'medical center', 'healthcare', 'clinic', 'institute']):
                clean = re.sub(r'^(the|a|an)\s+', '', after).strip()
                clean = re.sub(r'[^\w\s]', '', clean).strip()
                if clean:
                    return clean.title()
    return None


def extract_district(text: str):
    t = text.lower()
    if re.search(r'\bnorth district\b', t) or re.search(r'\b(in|from|across|within|around)\s+(the\s+)?north\b', t) or re.search(r'\bnorth\s+(zone|region|area|side)\b', t):
        return "North District"
    if re.search(r'\bsouth district\b', t) or re.search(r'\b(in|from|across|within|around)\s+(the\s+)?south\b', t) or re.search(r'\bsouth\s+(zone|region|area|side)\b', t):
        return "South District"
    if re.search(r'\bdowntown\b', t) or re.search(r'\bmetro center\b', t):
        return "Downtown"
    if re.search(r'\bnorth\b', t) and not re.search(r'\bnorth(field| central)\b', t) and not any(w in t for w in ['hospital', 'center', 'institute']):
        return "North District"
    if re.search(r'\bsouth\b', t) and not re.search(r'\bsouth(field| central)\b', t) and not any(w in t for w in ['hospital', 'center', 'institute']):
        return "South District"
    return None


def fast_rule_parser(q: str):
    q_low = q.lower().strip()

    # 1. Check Doctor name
    doc_name = None
    m = re.search(r'\b(?:dr\.?|doctor)\s+([a-z]+(?:\s+[a-z]+)?)', q_low)
    if m:
        candidate = m.group(1).strip()
        non_names = {'located', 'in', 'at', 'working', 'who', 'specialist', 'specializing', 'for', 'near'}
        words = candidate.split()
        if words and words[0] not in non_names:
            clean_cand = re.sub(r'(?i)\b(a|an|is|in|at|for|the|of)\b$', '', candidate).strip()
            doc_name = f"Dr. {clean_cand.title()}" if clean_cand else m.group(0).title()

    spec = extract_specialty(q_low)
    hosp = extract_hospital(q_low)
    dist = extract_district(q_low)

    # Specific doctor check
    if doc_name and spec:
        clean_doc = re.sub(r'(?i)\b(neurology|cardiology|dermatology|orthopedics|pediatrics|general medicine|doctor|dr\.?|department|at|in)\b', '', doc_name).strip()
        clean_doc = re.sub(r'(?i)\b(a|an|is|in|at|for|the|of)\b$', '', clean_doc).strip()
        clean_doc = re.sub(r'^[.\s]+', '', clean_doc).strip()
        final_name = f"Dr. {clean_doc.title()}" if (clean_doc and not clean_doc.lower().startswith("dr")) else (clean_doc or doc_name)
        ret = {
            'intent': 'doctor_and_specialty_check',
            'doctor_name': final_name,
            'specialty': spec
        }
        if hosp:
            ret['hospital_name'] = hosp
        return ret

    if doc_name and ('who is' in q_low or 'tell me about' in q_low or 'details' in q_low or 'info' in q_low or hosp or len(q_low.split()) <= 6):
        clean_doc = re.sub(r'(?i)\b(who is|tell me about|details|info|department|at|in)\b', '', doc_name).strip()
        if hosp:
            clean_doc = re.sub(r'(?i)\b' + re.escape(hosp.lower()) + r'\b', '', clean_doc).strip()
        clean_doc = re.sub(r'^[.\s]+', '', clean_doc).strip()
        final_name = f"Dr. {clean_doc.title()}" if (clean_doc and not clean_doc.lower().startswith("dr")) else (clean_doc or doc_name)
        ret = {
            'intent': 'doctor_by_name',
            'doctor_name': final_name
        }
        if hosp:
            ret['hospital_name'] = hosp
        return ret

    # 2. Doctors by Hospital (Takes precedence over general district)
    doctor_words = ['doctor', 'doctors', 'physician', 'physicians', 'practitioner', 'practitioners', 'specialist', 'specialists', 'staff', 'working', 'who works', 'show', 'list', 'view', 'find']
    if hosp and any(w in q_low for w in doctor_words) and not spec:
        return {
            'intent': 'doctors_by_hospital',
            'hospital_name': hosp
        }

    # 3. Specialty with Hospital / District / City
    if spec:
        if hosp:
            return {
                'intent': 'doctors_by_specialty_and_location',
                'specialty': spec,
                'location': hosp
            }
        if dist:
            return {
                'intent': 'doctors_by_specialty_and_district',
                'specialty': spec,
                'district': dist
            }

        loc = None
        for l in ['oak ridge', 'oakridge', 'bayview', 'highland', 'fairview', 'silver spring', 'metro central', 'north central', 'west end', 'riverdale', 'eastside']:
            if l in q_low:
                loc = l.title().replace('Oak Ridge', 'Oakridge')
                break

        if not loc:
            for prep in [' at ', ' in ', ' near ']:
                if prep in q_low:
                    raw_loc = q_low.split(prep)[-1].strip()
                    raw_loc = re.sub(r'\b(please|tell me|show me|find)\b', '', raw_loc).strip()
                    if raw_loc:
                        loc = raw_loc.title()
                        break

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

    # 4. Doctors by District / Hospital
    if any(w in q_low for w in ['doctor', 'doctors', 'physician', 'physicians', 'practitioner', 'practitioners', 'specialist', 'specialists']):
        if hosp:
            return {'intent': 'doctors_by_hospital', 'hospital_name': hosp}
        if dist:
            return {'intent': 'doctors_by_district', 'district': dist}

    # 5. Hospitals & non-specialty queries
    if 'hospital' in q_low or 'medical center' in q_low or 'facilities' in q_low:
        if ('doctor' in q_low or 'practitioner' in q_low or 'physician' in q_low or 'working' in q_low or 'who works' in q_low) and hosp:
            return {'intent': 'doctors_by_hospital', 'hospital_name': hosp}
        if dist:
            return {'intent': 'hospitals_by_district', 'district': dist}
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

    # 3. Check hospital
    hosp = extract_hospital(q_low)
    dist = extract_district(q_low)

    # If doctors at hospital
    if hosp and any(w in q_low for w in ['doctor', 'doctors', 'physician', 'physicians', 'practitioner', 'practitioners', 'specialist', 'specialists', 'staff', 'working', 'who works', 'show', 'list', 'view', 'find']) and not spec:
        return {"intent": "doctors_by_hospital", "hospital_name": hosp}

    # 4. Check locations / districts with specialty
    loc = None
    for l in ["oak ridge", "oakridge", "bayview", "highland", "fairview", "silver spring", "metro central", "north central", "west end", "riverdale", "eastside"]:
        if l in q_low:
            loc = l.title().replace("Oak Ridge", "Oakridge")
            break

    if not loc and not dist and not hosp:
        for prep in [' at ', ' in ', ' near ']:
            if prep in q_low:
                raw_loc = q_low.split(prep)[-1].strip()
                raw_loc = re.sub(r'\b(please|tell me|show me|find)\b', '', raw_loc).strip()
                if raw_loc:
                    loc = raw_loc.title()
                    break

    if spec and hosp:
        return {"intent": "doctors_by_specialty_and_location", "specialty": spec, "location": hosp}
    if spec and dist:
        return {"intent": "doctors_by_specialty_and_district", "specialty": spec, "district": dist}
    if spec and loc:
        return {"intent": "doctors_by_specialty_and_location", "specialty": spec, "location": loc}
    if spec:
        return {"intent": "doctors_by_specialty", "specialty": spec}

    # 5. Check general doctors by district/hospital
    if any(w in q_low for w in ['doctor', 'doctors', 'physician', 'physicians', 'practitioner', 'practitioners', 'specialist', 'specialists']):
        if hosp:
            return {"intent": "doctors_by_hospital", "hospital_name": hosp}
        if dist:
            return {"intent": "doctors_by_district", "district": dist}

    # 6. Check hospitals
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