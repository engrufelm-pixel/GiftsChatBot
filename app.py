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

# ===== Подключение к AITunnel =====
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL")
)

# ===== Загрузка каталога =====
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


# ===== Вспомогательные функции =====

def extract_budget(text):
    match = re.search(r"\d{3,6}", text.replace(" ", ""))
    return int(match.group()) if match else None


def detect_status(text):
    text = text.lower()
    if "топ" in text or "vip" in text or "директор" in text:
        return "топ-менеджмента"
    if "менеджер" in text:
        return "менеджеров"
    if "сотрудник" in text:
        return "сотрудников"
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


# ===== ROUTES =====

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")

    # 1️⃣ FAQ — первыми
    faq = answer_faq(user_message)
    if faq:
        time.sleep(1)
        return jsonify({"reply": faq})

    # 2️⃣ Бюджет
    budget = extract_budget(user_message)

    if not budget:
        return jsonify({"reply": "Пожалуйста, укажите бюджет (например: на сумму 5000 руб.)."})

    status = detect_status(user_message)
    company = detect_company(user_message)

    # 3️⃣ Фильтрация
    filtered = df[df["Цена_число"] <= budget]
    filtered = filtered.sort_values(by="Цена_число", ascending=False).head(8)

    if filtered.empty:
        return jsonify({"reply": "К сожалению, в указанном бюджете подходящих товаров не найдено."})

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

Правила:
- Не начинай каждый ответ с приветствия.
- Используй приветствие только если уместно.
- Используй только товары из списка.
- Не придумывай позиции.
- Не добавляй вымышленные характеристики.
- Не считай общую сумму.
- Не выходи за бюджет.
- Краткое деловое вступление + список 4–5 товаров.

Товары:
{catalog_text}
"""
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ]
        )

        return jsonify({"reply": response.choices[0].message.content})

    except Exception as e:
        print("Ошибка API:", e)
        return jsonify({"reply": "Ошибка соединения с AI. Попробуйте ещё раз."})


if __name__ == "__main__":
    app.run(port=5000)
