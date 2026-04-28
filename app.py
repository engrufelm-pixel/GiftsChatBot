import os
import re
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


def extract_budget(text):
    match = re.search(r"\d{3,6}", text.replace(" ", ""))
    return int(match.group()) if match else None


def detect_vip(text):
    text = text.lower()
    if "топ" in text or "vip" in text or "директор" in text:
        return True
    return False


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


def build_sets(budget, vip):
    filtered = df[df["Цена_число"] <= budget]

    if vip:
        filtered = filtered[filtered["Цена_число"] >= budget * 0.5]

    filtered = filtered.sort_values(by="Цена_число", ascending=False)

    # 1 подборка — премиальная
    set1 = filtered.head(5)

    # 2 подборка — средний ценовой сегмент
    mid = filtered[
        (filtered["Цена_число"] < budget * 0.8) &
        (filtered["Цена_число"] > budget * 0.4)
    ]
    set2 = mid.head(5)

    # 3 подборка — универсальная
    universal = filtered.sample(min(5, len(filtered)))
    set3 = universal

    return set1, set2, set3


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")

    faq = answer_faq(user_message)
    if faq:
        return jsonify({"reply": faq})

    budget = extract_budget(user_message)

    if not budget:
        return jsonify({"reply": "Пожалуйста, укажите бюджет (например: 5000 руб.)."})

    vip = detect_vip(user_message)

    set1, set2, set3 = build_sets(budget, vip)

    def format_set(title, dataset):
        text = f"\n{title}\n\n"
        for _, row in dataset.iterrows():
            text += (
                f"• {row['Название']}\n"
                f"  Цена: {row['Цена']} руб.\n"
                f"  Артикул: {row['Артикул']}\n"
                f"  Ссылка: https://gifts.ru/search/?q={row['Артикул']}\n\n"
            )
        return text

    catalog_text = (
        format_set("Вариант 1 — Премиальный:", set1) +
        format_set("Вариант 2 — Сбалансированный:", set2) +
        format_set("Вариант 3 — Универсальный:", set3)
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        max_tokens=400,
        messages=[
            {
                "role": "system",
                "content": """
Ты консультант gifts.ru.
Оформи аккуратно предложенные подборки.
Не придумывай товары.
Не добавляй вымышленные характеристики.
"""
            },
            {
                "role": "user",
                "content": catalog_text
            }
        ]
    )

    return jsonify({"reply": response.choices[0].message.content})


if __name__ == "__main__":
    app.run(port=5000)