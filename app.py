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

# ===== ЗАГРУЗКА И ОЧИСТКА КАТАЛОГА =====
df = pd.read_excel("catalog.xlsx")

# Убираем системный мусор
df = df[
    (~df["Название"].str.contains("Бренд|Размер|Свободно|На складе|В пути|Европа|Поиск|Найдено", na=False, case=False)) &
    (~df["Название"].str.contains(":", na=False)) &
    (df["Название"].str.len() > 5)
]

df["Цена_число"] = df["Цена"].astype(str).str.replace(",", ".").str.replace(" ", "")
df["Цена_число"] = pd.to_numeric(df["Цена_число"], errors="coerce")
df = df[df["Цена_число"].notna()]
df = df[df["Цена_число"] > 100]

# ===== БАЗА ЗНАНИЙ (ОТВЕТЫ НА ВОПРОСЫ КЛИЕНТА) =====
KNOWLEDGE_BASE = """
- Инструменты: Все условия и бонусы — в Личном кабинете на gifts.ru.
- Акт сверки: Запросить в Личном кабинете (раздел «Услуги») или через ЭДО.
- Срок производства: Данные загрузки обновляются каждые полчаса.
- Доставка: Пункты выдачи указаны в разделах «Контакты» и «Доставка».
- Новинки: Еженедельный дайджест приходит через подписку на рассылку.
- Склад: Данные по наличию и резервам обновляются в реальном времени.
- Образцы: Раздел «Маркетинговая поддержка» содержит образцы и каталоги.
- Маркировка: Товары с маркировкой «Честный знак» имеют специальный значок.
- Резерв: Ставится на 4 рабочих дня. Статус «Если освободится» — авторезерв при отмене чужого заказа.
- Нанесение: Срок от 3 до 14 дней. Минимальный тираж зависит от вида нанесения.
"""

def extract_budget(text):
    match = re.search(r"\d{3,6}", text.replace(" ", ""))
    return int(match.group()) if match else None

def get_item_type(name):
    name = name.lower()
    if "набор" in name: return "Набор"
    if "рюкзак" in name or "сумка" in name or "шопер" in name: return "Сумки"
    if "power" in name or "заряд" in name or "колонка" in name or "лампа" in name: return "Электроника"
    if "ежедневник" in name or "блокнот" in name: return "Офис"
    if "ручка" in name: return "Письмо"
    return "Другое"

def build_smart_selection(budget, is_vip):
    # 1. Сначала фильтруем по бюджету
    available = df[df["Цена_число"] <= budget].copy()
    
    if is_vip:
        # Убираем дешевые товары и "бытовуху" для ТОП-менеджеров
        available = available[~available["Название"].str.contains("Кружка|Чайник|Шляпа|Джибитс|Салфетка|Пакет", na=False, case=False)]
        available = available[available["Цена_число"] >= budget * 0.2]

    # 2. Сортируем: сначала самые дорогие (статусные)
    available = available.sort_values(by="Цена_число", ascending=False)
    
    selected = []
    used_types = set()
    
    # 3. Пытаемся взять товары РАЗНЫХ типов
    for _, row in available.iterrows():
        item_type = get_item_type(row["Название"])
        
        # Правило: только ОДИН набор в списке, чтобы не было однообразия
        if item_type == "Набор" and "Набор" in used_types:
            continue
            
        if item_type not in used_types:
            selected.append(row)
            used_types.add(item_type)
        
        if len(selected) == 5: break

    # 4. Если не набрали 5 разных типов, добираем просто по цене
    if len(selected) < 5:
        for _, row in available.iterrows():
            if not any(s["Артикул"] == row["Артикул"] for s in selected):
                selected.append(row)
            if len(selected) == 5: break
                
    return selected

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message", "")
    
    # 1. Проверяем бюджет
    budget = extract_budget(user_message)
    is_vip = any(word in user_message.lower() for word in ["лукойл", "топ", "vip", "директор", "руковод"])

    if budget:
        # Логика подбора товаров
        products = build_smart_selection(budget, is_vip)
        if not products:
            return jsonify({"reply": "В этом бюджете товаров не найдено. Попробуйте увеличить сумму."})
        
        product_list = "\n".join([f"- {p['Название']} ({p['Цена']} руб., арт. {p['Артикул']})" for p in products])
        context = f"ПОДБОРКА ТОВАРОВ:\n{product_list}"
    else:
        # Логика ответов на вопросы
        context = f"БАЗА ЗНАНИЙ:\n{KNOWLEDGE_BASE}"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"Ты эксперт gifts.ru. Твои данные:\n{context}\n\nИНСТРУКЦИЯ:\n1. Если есть товары, представь их списком из 5 позиций. Напиши ОДНУ короткую фразу в начале.\n2. Если это вопрос про сайт, ответь строго по базе знаний.\n3. Не пиши 'Здравствуйте' в каждом сообщении.\n4. Будь краток. Ссылки не давай."},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500,
            temperature=0.3
        )
        return jsonify({"reply": response.choices[0].message.content.strip()})
    except:
        return jsonify({"reply": "Произошла ошибка связи с ИИ. Попробуйте еще раз."})

if __name__ == "__main__":
    app.run(port=5000)