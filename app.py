import os
import re
import time
import random
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
    "shown_articles": []
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


# ===== ВСПОМОГАТЕЛЬНЫЕ =====

def extract_budget(text):
    match = re.search(r"\d{3,6}", text.replace(" ", ""))
    return int(match.group()) if match else None


def detect_status(text):
    text = text.lower()
    if "топ" in text or "vip" in text or "директор" in text:
        return "vip"
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


# ===== НОВАЯ ЛОГИКА ПОДБОРА =====

def build_selection(budget, status):
    filtered = df[df["Цена_число"] <= budget]

    if status == "vip":
        filtered = filtered[filtered["Цена_число"] >= budget * 0.5]

    # убираем уже показанные
    filtered = filtered[~filtered["Артикул"].isin(session_memory["shown_articles"])]

    # группируем по категориям
    grouped = filtered.groupby("Категория")

    selected = []

    for _, group in grouped:
        group = group.sort_values("Цена_число", ascending=False)
        selected.append(group.iloc[0])

    result = pd.DataFrame(selected)

    result = result.sort_values("Цена_число", ascending=False)

    return result.head(5)


# ===== ROUTES =====

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

    # "ЕЩЁ" — без OpenAI
    if any(word in user_message.lower() for word in ["еще", "ещё", "другие"]):
        if not session_memory["budget"]:
            return jsonify({"reply": "Сначала укажите бюджет."})

        products = build_selection(
            session_memory["budget"],
            session_memory["status"]
        )

        if products.empty:
            return jsonify({"reply": "Дополнительных вариантов больше нет."})

        session_memory["shown_articles"].extend(products["Артикул"].tolist())

        response = "Дополнительные варианты:\n\n"
        for _, row in products.iterrows():
            response += f"• {row['Название']} — {row['Цена']} руб. (Артикул {row['Артикул']})\n"

        return jsonify({"reply": response})

    # Новый запрос
    budget = extract_budget(user_message)
    status = detect_status(user_message)

    if not budget:
        return jsonify({"reply": "Пожалуйста, укажите бюджет (например: 5000 руб.)."})

    session_memory["budget"] = budget
    session_memory["status"] = status
    session_memory["shown_articles"] = []

    products = build_selection(budget, status)

    if products.empty:
        return jsonify({"reply": "К сожалению, подходящих товаров не найдено."})

    session_memory["shown_articles"].extend(products["Артикул"].tolist())

    catalog_text = "\n".join(
        products.apply(
            lambda row: f"{row['Название']} — {row['Цена']} руб. (Артикул {row['Артикул']})",
            axis=1
        ).tolist()
    )

    time.sleep(1.2)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        max_tokens=300,
        messages=[
            {
                "role": "system",
                "content": f"""
Ты консультант gifts.ru.
Не начинай каждый ответ с приветствия.
Краткое деловое вступление + список товаров.
Не придумывай позиции.

Товары:
{catalog_text}
"""
            },
            {"role": "user", "content": user_message}
        ]
    )

    return jsonify({"reply": response.choices[0].message.content})


if __name__ == "__main__":
    app.run(port=5000)