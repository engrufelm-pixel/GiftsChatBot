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

# ===== ЗАГРУЗКА И ЧИСТКА КАТАЛОГА =====
df = pd.read_excel("catalog.xlsx")

# Очистка мусора
df = df[
    (~df["Название"].str.contains("Бренд|Размер|Свободно|На складе|В пути|Европа|Поиск|Найдено", na=False, case=False)) &
    (~df["Название"].str.contains(":", na=False)) &
    (df["Название"].str.len() > 5)
]

df["Цена_число"] = df["Цена"].astype(str).str.replace(",", ".").str.replace(" ", "")
df["Цена_число"] = pd.to_numeric(df["Цена_число"], errors="coerce")
df = df[df["Цена_число"].notna()]
df = df[df["Цена_число"] > 100]

# ===== БАЗА ЗНАНИЙ =====
KNOWLEDGE_BASE = """
- Акт сверки: Запросить в Личном кабинете (раздел «Услуги») или через ЭДО.
- Срок производства: Данные обновляются каждые полчаса.
- Доставка: Пункты выдачи в разделах «Контакты» и «Доставка».
- Новинки: Подпишитесь на рассылку для получения дайджеста.
- Резерв: Обычно 4 рабочих дня.
- Нанесение логотипа: Срок от 3 до 14 дней.
"""

def extract_budget(text):
    match = re.search(r"\d{3,6}", text.replace(" ", ""))
    return int(match.group()) if match else None

def build_selection(budget, is_vip):
    available = df[df["Цена_число"] <= budget].copy()
    
    if is_vip:
        # ЖЕСТКИЙ ФИЛЬТР: Убираем всё, что не солидно для ТОП-менеджмента
        bad_words = ["подставка", "брелок", "салфетка", "пакет", "кружка", "чайник", "шляпа", "чехол", "джибитс", "ручка шариковая"]
        for word in bad_words:
            available = available[~available["Название"].str.contains(word, na=False, case=False)]
        # Для VIP берем товары подороже (от 20% бюджета), чтобы не предлагать копеечные вещи
        available = available[available["Цена_число"] >= budget * 0.2]

    # Сортируем: сначала самые дорогие
    available = available.sort_values(by="Цена_число", ascending=False)
    
    # Берем топ-15 и из них выбираем 5 случайных для разнообразия, чтобы не одни наборы были
    top_pool = available.head(15)
    if len(top_pool) >= 5:
        selected = top_pool.sample(5)
    else:
        selected = top_pool

    return selected

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message", "")
    budget = extract_budget(user_message)
    is_vip = any(word in user_message.lower() for word in ["лукойл", "топ", "vip", "директор", "руковод"])

    if budget:
        products = build_selection(budget, is_vip)
        if products.empty:
            return jsonify({"reply": "В этом бюджете подходящих статусных товаров не найдено."})
        
        product_list = "\n".join([f"- {p['Название']} ({p['Цена']} руб, арт. {p['Артиucл'] if 'Артикул' in p else p.get('Артикул', 'н/д')})" for _, p in products.iterrows()])
        context = f"СПИСОК ТОВАРОВ ДЛЯ ПРЕДЛОЖЕНИЯ:\n{product_list}"
    else:
        context = f"БАЗА ЗНАНИЙ (ОТВЕТЫ НА ВОПРОСЫ):\n{KNOWLEDGE_BASE}"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"Ты эксперт подарков gifts.ru. Твои данные:\n{context}\n\nПРАВИЛА:\n1. Предложи ровно 5 товаров из списка (если их меньше в списке - дай сколько есть).\n2. НЕ суммируй цены! Это разные варианты подарков на выбор.\n3. В начале напиши ОДНУ короткую вежливую фразу.\n4. Если это вопрос про сайт, ответь по базе знаний.\n5. Не давай ссылки. Используй деловой стиль."},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500,
            temperature=0.5
        )
        return jsonify({"reply": response.choices[0].message.content.strip()})
    except Exception as e:
        print(e)
        return jsonify({"reply": "Ошибка связи. Попробуйте еще раз."})

if __name__ == "__main__":
    app.run(port=5000)