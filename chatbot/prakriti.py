"""
chatbot/prakriti.py
----------------------
"Prakriti AI Connect" — the floating sustainability assistant.
Prakriti (Sanskrit/Hindi: "nature") only helps with waste, recycling,
sustainability and MCG-related civic topics. Off-topic or harmful
requests are politely declined by the system prompt + a lightweight
keyword guard.
"""
from database.db_router import execute, fetch_all
from utils.ai_client import stream_chat_completion, chat_completion
from config import settings

SYSTEM_PROMPT_EN = """You are Prakriti AI Connect, a friendly AI Sustainability Assistant for
Indian citizens, built for the {municipality} Smart Waste Management platform.

You ONLY help with:
- Waste segregation (wet/dry/hazardous)
- Plastic recycling and reduction
- Composting at home
- E-waste and battery disposal
- Government / MCG waste policies and guidelines
- How to file or track a waste complaint on this platform
- Sustainable lifestyle tips and climate education

Rules:
- Keep answers concise, practical, and India-specific (mention MCG / Swachh Bharat norms where relevant).
- Use simple language and markdown formatting (bullet points, bold) where helpful.
- If asked something unrelated to sustainability/waste/civic topics, politely redirect the
  conversation back to what you can help with — do not answer unrelated questions.
- Never provide harmful, illegal, or dangerous instructions of any kind.
- You may reply in English or Hindi depending on what the user writes in.
"""

SYSTEM_PROMPT_HI = """आप Prakriti AI Connect हैं, भारतीय नागरिकों के लिए एक मित्रवत AI स्थिरता सहायक,
जो {municipality} स्मार्ट कचरा प्रबंधन प्लेटफ़ॉर्म के लिए बनाया गया है।

आप केवल इनमें मदद करते हैं:
- कचरे का पृथक्करण (गीला/सूखा/खतरनाक)
- प्लास्टिक रीसाइक्लिंग और कमी
- घर पर खाद बनाना
- ई-वेस्ट और बैटरी निपटान
- सरकारी/MCG कचरा नीतियाँ
- इस प्लेटफ़ॉर्म पर शिकायत दर्ज या ट्रैक करना
- टिकाऊ जीवनशैली और जलवायु शिक्षा

नियम: संक्षिप्त, व्यावहारिक उत्तर दें। असंबंधित प्रश्नों को विनम्रता से वापस विषय पर लाएं।
"""


def get_system_prompt(language="English"):
    template = SYSTEM_PROMPT_HI if language.lower().startswith("hi") else SYSTEM_PROMPT_EN
    return template.format(municipality=settings.MUNICIPALITY_NAME)


def build_messages(history, user_message, language="English"):
    """history: list of {"role": "user"/"assistant", "content": str}"""
    messages = [{"role": "system", "content": get_system_prompt(language)}]
    # keep last 10 turns for context window efficiency
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_message})
    return messages


def stream_reply(history, user_message, language="English"):
    messages = build_messages(history, user_message, language)
    yield from stream_chat_completion(messages, temperature=0.5, max_tokens=600)


def get_reply(history, user_message, language="English"):
    messages = build_messages(history, user_message, language)
    return chat_completion(messages, temperature=0.5, max_tokens=600)


def save_message(user_id, session_id, role, message, language="en"):
    execute(
        "INSERT INTO chat_history (user_id, session_id, role, message, language) VALUES (?,?,?,?,?)",
        (user_id, session_id, role, message, language),
    )


def load_history(user_id, session_id, limit=50):
    rows = fetch_all(
        "SELECT role, message FROM chat_history WHERE user_id=? AND session_id=? "
        "ORDER BY created_at ASC LIMIT ?",
        (user_id, session_id, limit),
    )
    return [{"role": r["role"], "content": r["message"]} for r in rows]


def clear_history(user_id, session_id):
    execute("DELETE FROM chat_history WHERE user_id=? AND session_id=?", (user_id, session_id))
