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

# ===== ЗАГРУЗКА КАТАЛОГА =====
df = pd.read_excel("catalog.xlsx")

required_columns = ["Название", "Цена", "Артикул", "Категория"]
for col in required_columns:
    if col not in df.columns:
        raise Exception(f"В файле catalog.xlsx нет колонки: {col}")

df = df[required_columns].copy()

# ===== ЧИСТКА ДАННЫХ =====
df["Название"] = df["Название"].astype(str).str.strip()
df["Цена"] = df["Цена"].astype(str).str.strip()
df["Артикул"] = df["Артикул"].astype(str).str.strip()
df["Категория"] = df["Категория"].astype(str).fillna("").str.strip()

df = df[
    (df["Название"].str.len() > 3) &
    (~df["Название"].str.contains(
        "Бренд|Размер|Свободно|На складе|В пути|Европа|Поиск|Найдено|Артикул",
        case=False, na=False
    ))
]

df["Цена_число"] = (
    df["Цена"]
    .str.replace("от ", "", regex=False)
    .str.replace("₽", "", regex=False)
    .str.replace("руб.", "", regex=False)
    .str.replace("руб", "", regex=False)
    .str.replace(",", ".", regex=False)
    .str.replace(" ", "", regex=False)
    .str.replace("\xa0", "", regex=False)
)

df["Цена_число"] = pd.to_numeric(df["Цена_число"], errors="coerce")
df = df[df["Цена_число"].notna()]
df = df[df["Цена_число"] > 10]

df = df.drop_duplicates(subset=["Название", "Цена_число", "Артикул"]).reset_index(drop=True)


# ===== ПЕРЕВОД КАТЕГОРИЙ НА РУССКИЙ =====
def translate_category(cat):
    c = str(cat).lower().strip()
    
    mapping = {
        "podarochnye_nabory": "Подарочные наборы",
        "posuda": "Посуда",
        "elektronika": "Электроника",
        "sumki": "Сумки и аксессуары",
        "tekstil": "Текстиль и одежда",
        "ofis": "Офис и канцелярия",
        "vip": "VIP подарки",
        "zonty": "Зонты",
        "otdyh": "Отдых и туризм",
        "dom": "Дом и интерьер",
        "upakovka": "Упаковка",
        "nagrady": "Награды",
        "krasota": "Красота и здоровье",
        "kantselyariya": "Канцелярия"
    }
    
    for key, val in mapping.items():
        if key in c:
            return val
            
    c = c.replace("_", " ").capitalize()
    return c if c else "Без категории"

df["Категория_рус"] = df["Категория"].apply(translate_category)


# ===== БАЗА ЗНАНИЙ =====
KNOWLEDGE_BASE = """
О компании: gifts.ru / Проект 111 — поставщик бизнес-подарков.
Доставка: Пункты выдачи в Москве, СПб, Новосибирске. Есть доставка ТК.
Оплата: Безналичная оплата по счету.
Нанесение: Шелкография, гравировка, УФ-печать и др.
Резерв: Стандартно 4 рабочих дня.
"""

def extract_budget(text):
    text = text.lower().replace("\xa0", " ").strip()
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(к|тыс|тысяч)', text)
    if m:
        value = float(m.group(1).replace(",", "."))
        return int(value * 1000)
    text_no_spaces = text.replace(" ", "")
    m2 = re.search(r'(\d{3,6})', text_no_spaces)
    if m2:
        return int(m2.group(1))
    return None

def detect_vip(text):
    vip_words = ["лукойл", "газпром", "роснефть", "топ-менедж", "топ менедж", "топ", "vip", "вип", "директор", "руковод", "ceo"]
    return any(word in text.lower() for word in vip_words)

def detect_gender(text):
    female_words = ["жене", "женщин", "девушк", "маме", "подруге"]
    male_words = ["мужу", "мужчин", "парню", "папе", "другу", "коллеге муж"]
    female = any(word in text.lower() for word in female_words)
    male = any(word in text.lower() for word in male_words)
    if female and not male: return "female"
    if male and not female: return "male"
    return "neutral"

def detect_query_type(text):
    if extract_budget(text): return "selection"
    if any(w in text.lower() for w in ["подбери", "подбор", "подарок", "варианты"]): return "selection"
    return "question"


def build_selection(budget, is_vip, gender, query_text):
    available = df[df["Цена_число"] <= budget].copy()
    if available.empty: return available

    # ЖЕСТКИЙ ФИЛЬТР МУСОРА ДЛЯ VIP
    if is_vip:
        # Убираем "нестатусные" слова навсегда
        bad_words = [
            "чайник", "подставка", "брелок", "кружка", "стакан", "футболка", 
            "шоппер", "значок", "ручка шариковая", "карандаш", "открывалка", 
            "зажигалка", "чехол", "коврик", "шнурок", "джибитс", "лейбл", 
            "салфетка", "пакет", "антистресс", "шляпа", "панама", "носки"
        ]
        pattern = "|".join(bad_words)
        available = available[~available["Название"].str.contains(pattern, case=False, na=False)]
        
        # Сначала ищем реально дорогие товары (от 40% бюджета)
        vip_strict = available[available["Цена_число"] >= budget * 0.4]
        
        # Если дорогих товаров меньше 5 штук, снижаем планку до 20% от бюджета
        if len(vip_strict) < 5:
            available = available[available["Цена_число"] >= budget * 0.2]
        else:
            available = vip_strict
            
    else:
        available = available[available["Цена_число"] >= max(50, budget * 0.1)]

    # Фильтр по полу
    if gender == "female":
        available = available[~available["Название"].str.contains("мужск|жилет|дождевик|бритв", case=False, na=False)]
    if gender == "male":
        available = available[~available["Название"].str.contains("женск|косметичк|ваза", case=False, na=False)]

    if available.empty: return available

    # Сортируем: дорогие и статусные идут первыми
    available = available.sort_values(by="Цена_число", ascending=False)

    result_rows = []
    used_categories = set()
    used_names = set()

    # ШАГ 1: Берем по одному товару из РАЗНЫХ категорий
    for _, row in available.iterrows():
        cat = row["Категория_рус"]
        name = row["Название"]

        if name in used_names: continue

        if cat not in used_categories:
            result_rows.append(row)
            used_categories.add(cat)
            used_names.add(name)

        if len(result_rows) == 5: break

    # ШАГ 2: Если 5 разных категорий не набралось, добиваем лучшими из того, что осталось
    if len(result_rows) < 5:
        for _, row in available.iterrows():
            name = row["Название"]
            if name not in used_names:
                result_rows.append(row)
                used_names.add(name)
            if len(result_rows) == 5: break

    return pd.DataFrame(result_rows).head(5)


def format_products_reply(products, is_vip):
    intro = "Подобрал 5 отличных вариантов в указанном бюджете:"
    if is_vip:
        intro = "Подготовил подборку статусных премиум-подарков:"

    lines = [intro, ""]

    for i, (_, p) in enumerate(products.iterrows(), start=1):
        name = str(p["Название"]).strip()
        price = int(p["Цена_число"]) if float(p["Цена_число"]).is_integer() else p["Цена_число"]
        article = str(p["Артикул"]).strip()
        category = str(p["Категория_рус"]).strip()

        lines.append(f"{i}. {name}")
        lines.append(f"Категория: {category}")
        lines.append(f"Цена: {price} руб.")
        lines.append(f"Артикул: {article}")
        lines.append("")

    return "\n".join(lines).strip()


def answer_question_with_ai(user_message):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"Ты консультант gifts.ru. Отвечай по базе: {KNOWLEDGE_BASE}. Без звездочек, кратко."},
            {"role": "user", "content": user_message}
        ],
        max_tokens=300,
        temperature=0.2
    )
    return response.choices[0].message.content.strip().replace("*", "")


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    try:
        user_message = request.json.get("message", "").strip()
        if not user_message:
            return jsonify({"reply": "Напишите запрос, и я помогу с подбором."})

        query_type = detect_query_type(user_message)
        budget = extract_budget(user_message)
        is_vip = detect_vip(user_message)
        gender = detect_gender(user_message)

        if query_type == "selection":
            if not budget:
                return jsonify({"reply": "Укажите, пожалуйста, бюджет, и я подберу 5 подходящих вариантов."})

            products = build_selection(budget, is_vip, gender, user_message)

            if products.empty:
                return jsonify({"reply": "К сожалению, в указанном бюджете подходящих статусных товаров не найдено. Попробуйте увеличить бюджет."})

            reply = format_products_reply(products, is_vip)
            return jsonify({"reply": reply})

        reply = answer_question_with_ai(user_message)
        return jsonify({"reply": reply})

    except Exception as e:
        print("Ошибка:", e)
        return jsonify({"reply": "Произошла ошибка. Попробуйте еще раз."})

if __name__ == "__main__":
    app.run(port=5000)