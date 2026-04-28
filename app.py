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


def extract_budget(text):
    match = re.search(r"\d{3,6}", text.replace(" ", ""))
    return int(match.group()) if match else None


def detect_vip(text):
    text = text.lower()
    return "топ" in text or "vip" in text or "директор" in text


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


def build_selection(budget, vip):
    filtered = df[df["Цена_число"] <= budget]

    if vip:
        filtered = filtered[filtered["Цена_число"] >= budget * 0.5]

    filtered = filtered.sort_values(by="Цена_число", ascending=False)

    selected = []
    used_categories = set()

    for _, row in filtered.iterrows():
        if row["Категория"] not in used_categories:
            selected.append(row)
            used_categories.add(row["Категория"])
        if len(selected) == 5:
            break

    return selected


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")

    # FAQ
    faq = answer_faq(user_message)
    if faq:
        return jsonify({"reply": faq})

    budget = extract_budget(user_message)
    if not budget:
        return jsonify({"reply": "Пожалуйста, укажите бюджет (например: 5000 руб.)."})

    vip = detect_vip(user_message)

    products = build_selection(budget, vip)

    if not products:
        return jsonify({"reply": "К сожалению, подходящих товаров не найдено."})

    # ✅ GPT получает только реальные товары
    product_list = "\n".join(
        [
            f"{row['Название']} — {row['Цена']} руб. (Артикул {row['Артикул']})"
            for row in products
        ]
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.1,
        max_tokens=180,
        messages=[
            {
                "role": "system",
                "content": """
Ты консультант gifts.ru.
Оформи кратко и делово список товаров.
Не добавляй новые позиции.
Не изменяй цены.
Не добавляй характеристик.
"""
            },
            {
                "role": "user",
                "content": product_list
            }
        ]
    )

    return jsonify({"reply": response.choices[0].message.content})


if __name__ == "__main__":
    app.run(port=5000)