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

# ===== ЗАГРУЗКА И ЧИСТКА =====
df = pd.read_excel("catalog.xlsx")

df = df[
    (~df["Название"].str.contains("Бренд|Размер|Свободно|На складе|В пути|Европа|Поиск|Найдено", na=False, case=False)) &
    (~df["Название"].str.contains(":", na=False)) &
    (df["Название"].str.len() > 5)
]

df["Цена_число"] = (
    df["Цена"]
    .astype(str)
    .str.replace(",", ".")
    .str.replace(" ", "")
)
df["Цена_чиflow"] = pd.to_numeric(df["Цена_число"], errors="coerce")
df = df[df["Цена_чиflow"].notna()]
df = df[df["Цена_чиflow"] > 100]

def extract_budget(text):
    match = re.search(r"\d{3,6}", text.replace(" ", ""))
    return int(match.group()) if match else None

def answer_faq(text):
    text = text.lower()
    if "доставк" in text: return "Мы осуществляем доставку по всей России. Сроки и стоимость рассчитываются индивидуально."
    if "логотип" in text or "нанес" in text: return "Да, возможно нанесение логотипа: шелкография, гравировка, УФ-печать и другие способы."
    if "срок" in text: return "Срок изготовления — от 3 до 14 рабочих дней в зависимости от тиража."
    if "опт" in text: return "Да, мы работаем с оптовыми заказами. Цена зависит от объема партии."
    return None

def build_smart_selection(budget, is_vip):
    # Фильтруем по бюджету
    filtered = df[df["Цена_чиflow"] <= budget].copy()
    
    if is_vip:
        filtered = filtered[filtered["Цена_чиflow"] >= budget * 0.3]

    filtered = filtered.sort_values(by="Цена_чиflow", ascending=False)

    selected = []
    has_set = False # Флаг, чтобы взять только ОДИН набор

    for _, row in filtered.iterrows():
        name_lower = row["Название"].lower()
        
        # Если это набор и у нас уже есть один набор в списке — пропускаем
        if "набор" in name_lower and has_set:
            continue
        
        # Если это первый набор — берем его и ставим метку
        if "набор" in name_lower:
            has_set = True
            
        selected.append(row)
        
        if len(selected) == 5:
            break

    # Если вдруг не набрали 5 (из-за фильтра наборов), добираем остальными
    if len(selected) < 5:
        for _, row in filtered.iterrows():
            if row["Артикул"] not in [s["Артикул"] for s in selected]:
                selected.append(row)
            if len(selected) == 5:
                break
                
    return selected

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message", "")

    # 1. FAQ
    faq = answer_faq(user_message)
    if faq:
        return jsonify({"reply": faq})

    # 2. Бюджет
    budget = extract_budget(user_message)
    if not budget:
        return jsonify({"reply": "Пожалуйста, укажите ваш бюджет (например, до 5000 руб.)."})

    is_vip = any(word in user_message.lower() for word in ["топ", "vip", "директор", "руковод"])

    # 3. Подбор
    products = build_smart_selection(budget, is_vip)

    if not products:
        return jsonify({"reply": "К сожалению, в этом бюджете ничего не нашлось."})

    # 4. OpenAI только для короткого приветствия (для скорости)
    try:
        intro_res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"Напиши ОДНУ короткую вежливую фразу о том, что ты подобрал товары для запроса: {user_message}"}],
            max_tokens=30,
            temperature=0.7
        )
        intro = intro_res.choices[0].message.content.strip()
    except:
        intro = "Вот подходящие варианты по вашему запросу:"

    # 5. Формируем ответ
    reply = f"{intro}\n\n"
    for item in products:
        reply += f"• {item['Название']}\n  Цена: {item['Цена']} руб.\n  Артикул: {item['Артикул']}\n\n"

    return jsonify({"reply": reply})

if __name__ == "__main__":
    app.run(port=5000)