import os
import re
import time
import pandas as pd
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

app = Flask(__name__)
CORS(app)

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL")
)

# ===== ПАМЯТЬ =====
session_memory = {
    "budget": None,
    "status": None,
    "company": None
}

# ===== КАТАЛОГ =====
df = pd.read_excel("catalog.xlsx")

df = df[
    (~df["Название"].str.contains("Бренд|Размер|Свободно|На складе|В пути|Европа|Поиск|Найдено", na=False)) &
    (~df["Название"].str.contains(":", na=False)) &
    (df["Название"].str.len() > 5)
]

df["Цена_число"] = (
    df["Цена"]
    .astype(str)
    .str.replace(",", ".")
    .str.replace(" ", "")
)

df["Цена_число"] = pd.to_numeric(df["Цена_число"], errors="coerce")
df = df[df["Цена_число"].notna()]
df = df[df["Цена_число"] > 100]


# ===== ФУНКЦИИ =====

def extract_budget(text):
    match = re.search(r"\d{3,6}", text.replace(" ", ""))
    return int(match.group()) if match else None


def detect_status(text):
    text = text.lower()
    if "топ" in text or "vip" in text or "директор" in text:
        return "vip"
    if "менеджер" in text:
        return "manager"
    if "сотрудник" in text:
        return "staff"
    return None


def detect_company(text):
    match = re.search(r"компани[ия]\s+([а-яa-zA-Z0-9]+)", text.lower())
    if match:
        return match.group(1).capitalize()
    return None


def answer_faq(text):
    text = text.lower()

    if "доставк" in text:
        return "Мы осуществляем доставку по всей России. Сроки и стоимость рассчитываются индивидуально."

    if "логотип" in text or "нанес" in text:
        return "Да, возможно нанесение логотипа различными способами: шелкография, гравировка, УФ-печать и другие."

    if "срок" in text:
        return "Срок изготовления зависит от объёма и типа нанесения. В среднем от 3 до 14 рабочих дней."

    if "опт" in text:
        return "Да, предусмотрены оптовые условия. Стоимость зависит от тиража."

    return None


def pick_products(filtered, budget, status):
    if status == "vip":
        exclude_words = ["кружк", "чайник", "шляп", "подставк"]
        for word in exclude_words:
            filtered = filtered[~filtered["Название"].str.lower().str.contains(word)]

        filtered = filtered[filtered["Цена_число"] >= budget * 0.6]
        filtered = filtered.sort_values(by="Цена_число", ascending=False)

    else:
        filtered = filtered.sort_values(by="Цена_число", ascending=False)

    return filtered.head(5)


# ===== ROUTES =====

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")

    # 1️⃣ FAQ
    faq = answer_faq(user_message)
    if faq:
        time.sleep(1)
        return jsonify({"reply": faq})

    # 2️⃣ Проверка "ещё"
    if any(word in user_message.lower() for word in ["еще", "ещё", "другие", "варианты"]):
        if session_memory["budget"]:
            budget = session_memory["budget"]
            status = session_memory["status"]
            company = session_memory["company"]
        else:
            return jsonify({"reply": "Сначала укажите бюджет и параметры подбора."})
    else:
        budget = extract_budget(user_message)
        status = detect_status(user_message)
        company = detect_company(user_message)

        if budget:
            session_memory["budget"] = budget
        if status:
            session_memory["status"] = status
        if company:
            session_memory["company"] = company

    if not session_memory["budget"]:
        return jsonify({"reply": "Пожалуйста, укажите бюджет (например: на сумму 5000 руб.)."})

    budget = session_memory["budget"]
    status = session_memory["status"]
    company = session_memory["company"]

    filtered = df[df["Цена_число"] <= budget]

    if filtered.empty:
        return jsonify({"reply": "К сожалению, в указанном бюджете подходящих товаров не найдено."})

    filtered = pick_products(filtered, budget, status)

    catalog_text = "\n".join(
        filtered.apply(
            lambda row: f"{row['Название']} — {row['Цена']} руб. (Артикул {row.get('Артикул','')})",
            axis=1
        ).tolist()
    )

    time.sleep(1.2)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            max_tokens=350,
            messages=[
                {
                    "role": "system",
                    "content": f"""
Ты консультант gifts.ru.

Не начинай каждый ответ с приветствия.
Используй только товары из списка.
Не придумывай позиции.
Не добавляй вымышленные характеристики.
Не считай общую сумму.
Краткое деловое вступление + список 4–5 товаров.

Товары:
{catalog_text}
"""
                },
                {"role": "user", "content": user_message}
            ]
        )

        return jsonify({"reply": response.choices[0].message.content})

    except Exception as e:
        print("Ошибка API:", e)
        return jsonify({"reply": "Ошибка соединения с AI. Попробуйте ещё раз."})


if __name__ == "__main__":
    app.run(port=5000)