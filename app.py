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

# Базовая очистка от мусора
df = df[
    (~df["Название"].str.contains("Бренд|Размер|Свободно|На складе|В пути|Европа|Поиск|Найдено", na=False, case=False)) &
    (~df["Название"].str.contains(":", na=False)) &
    (df["Название"].str.len() > 5)
]

df["Цена_число"] = df["Цена"].astype(str).str.replace(",", ".").str.replace(" ", "")
df["Цена_число"] = pd.to_numeric(df["Цена_число"], errors="coerce")
df = df[df["Цена_число"].notna()]
df = df[df["Цена_число"] > 100]

# ===== БАЗА ЗНАНИЙ (ЭКСКУРСИЯ ПО САЙТУ) =====
KNOWLEDGE_BASE = """
Инструменты и Личный кабинет:
- Условия работы, бонусы и заказы: в Личном кабинете на gifts.ru.
- Акт сверки: запросить в Личном кабинете (раздел «Услуги») или через ЭДО.
- Пункты выдачи: разделы «Контакты» и «Доставка».
- Дайджест новинок: подписаться на рассылки.
- Обновление склада и резервов: в реальном времени.
- Раздел с образцами, каталогами и раскладками: «Маркетинговая поддержка».
- Памятка по сигнальным образцам: в разделе «Помощь».

Товары и Нанесение:
- Размеры товара: смотреть поле «Размеры» или фото/видео рядом с людьми.
- Источник вдохновения: вкладки «Примеры» и «Фотографии» в галерее изображений.
- Цены: публичная цена сайта и цена с партнерской скидкой.
- Методы нанесения: выпадающий список «Добавить нанесение» в блоке с ценами.
- Оригинал-макеты: векторный файл ("конструктор") на вкладке «Файлы», тех. требования в карточке товара.
- Фото примеров печати: вкладка «Примеры» в галерее изображений.

Заказы и Резервы:
- Срок резерва: обычно 4 рабочих дня (на новогодние и блокирующие — меньше).
- Статус «Если освободится»: автоматический резерв, если предыдущий истечет.
- Перенос резерва: можно через инструмент «Переместить резерв».
- Минимальный тираж с лого: зависит от товара и вида нанесения.
- Срок нанесения: от 3 до 14 рабочих дней (точнее — в графике загрузки производства).
- БПЗ: Бланк подтверждения заказа. Счёт приходит после подтверждения БПЗ.
"""

def extract_budget(text):
    match = re.search(r"\d{3,6}", text.replace(" ", ""))
    return int(match.group()) if match else None

def is_vip_query(text):
    return any(word in text.lower() for word in ["топ", "vip", "директор", "руковод", "лукойл", "премиум"])

def build_smart_selection(budget, vip):
    filtered = df[df["Цена_число"] <= budget].copy()
    
    if vip:
        # Для VIP убираем бытовуху и берем товары дороже 30% от бюджета
        filtered = filtered[~filtered["Название"].str.contains("Чайник|Кружка|Шляпа|Джибитс|Подставк", na=False, case=False)]
        filtered = filtered[filtered["Цена_чиflow"] >= budget * 0.3] if "Цена_чиflow" in filtered else filtered

    # Сортируем по убыванию цены
    filtered = filtered.sort_values(by="Цена_число", ascending=False)

    selected = []
    used_categories = set()
    
    # Пытаемся набрать 5 товаров из РАЗНЫХ категорий
    for _, row in filtered.iterrows():
        cat = row["Категория"]
        if cat not in used_categories:
            selected.append(row)
            used_categories.add(cat)
        if len(selected) == 5: break
            
    # Если категорий не хватило, добираем просто по цене
    if len(selected) < 5:
        for _, row in filtered.iterrows():
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
    
    # 1. Проверяем, не вопрос ли это по базе знаний (FAQ)
    # Если вопрос не содержит бюджет, но содержит слова из базы знаний
    budget = extract_budget(user_message)
    is_vip = is_vip_query(user_message)

    # 2. Формируем контекст для GPT
    if budget:
        products = build_smart_selection(budget, is_vip)
        context_data = "ПОДОБРАННЫЕ ТОВАРЫ:\n" + "\n".join([f"- {p['Название']} ({p['Цена']} руб, арт. {p['Артикул']})" for p in products])
    else:
        context_data = "ИНФОРМАЦИЯ О САЙТЕ И УСЛОВИЯХ:\n" + KNOWLEDGE_BASE

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"Ты консультант gifts.ru. Твоя база данных:\n{context_data}\n\nПРАВИЛА:\n1. Если есть товары, предложи их списком (название, цена, артикул).\n2. Если это вопрос про сайт (акт сверки, личный кабинет и т.д.), ответь четко по базе.\n3. Стиль деловой, без лишних приветствий. Для Лукойла/VIP не предлагай чайники и кружки."},
                {"role": "user", "content": user_message}
            ],
            max_tokens=450,
            temperature=0.3
        )
        reply = response.choices[0].message.content.strip()
        return jsonify({"reply": reply})
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"reply": "Извините, возникла техническая ошибка. Попробуйте еще раз."})

if __name__ == "__main__":
    app.run(port=5000)