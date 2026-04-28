import re
import pandas as pd
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ===== ЗАГРУЗКА КАТАЛОГА =====
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
df = df[df["Цена_число"] > 100]


# ===== ВСПОМОГАТЕЛЬНЫЕ =====

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


def detect_type(name):
    name = name.lower()

    if "набор" in name:
        return "set"
    if "термос" in name or "кружк" in name or "чайник" in name:
        return "drinkware"
    if "ежедневник" in name or "блокнот" in name:
        return "stationery"
    if "рюкзак" in name or "сумк" in name:
        return "bags"
    if "power" in name or "заряд" in name:
        return "tech"
    if "ручк" in name:
        return "pen"
    return "other"


def build_selection(budget, vip):
    filtered = df[df["Цена_число"] <= budget]

    if vip:
        filtered = filtered[filtered["Цена_число"] >= budget * 0.4]

    filtered = filtered.sort_values(by="Цена_число", ascending=False)

    selected = []
    used_types = set()

    for _, row in filtered.iterrows():
        t = detect_type(row["Название"])

        if t not in used_types:
            selected.append(row)
            used_types.add(t)

        if len(selected) == 5:
            break

    # если меньше 5 — добираем просто по цене
    if len(selected) < 5:
        for _, row in filtered.iterrows():
            if row["Артикул"] not in [r["Артикул"] for r in selected]:
                selected.append(row)
            if len(selected) == 5:
                break

    return selected


# ===== ROUTES =====

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

    products = build_selection(budget, vip)

    if not products:
        return jsonify({"reply": "К сожалению, подходящих товаров не найдено."})

    response_text = f"Подборка в бюджете до {budget} руб:\n\n"

    for row in products:
        response_text += (
            f"• {row['Название']}\n"
            f"  Цена: {row['Цена']} руб.\n"
            f"  Артикул: {row['Артикул']}\n"
            f"  Ссылка: https://gifts.ru/search/?q={row['Артикул']}\n\n"
        )

    return jsonify({"reply": response_text})


if __name__ == "__main__":
    app.run(port=5000)