import os
import re
from difflib import SequenceMatcher

import pandas as pd
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
from jinja2 import TemplateNotFound

load_dotenv()

app = Flask(__name__)
CORS(app)

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

CATALOG_FILE = os.getenv("CATALOG_FILE", "catalog.xlsx")
PRICE_FILE = os.getenv("PRICE_FILE", "prices.xlsx")
FAQ_FILE = os.getenv("FAQ_FILE", "faq_clean.txt")

client = None
if OPENAI_API_KEY:
    client = OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL or None
    )

FALLBACK_FAQ_REPLY = "Точный ответ лучше уточнить у менеджера или в личном кабинете gifts.ru."


# =========================
# ОБЩИЕ ВСПОМОГАТЕЛЬНЫЕ
# =========================

def normalize_text(text):
    text = str(text).lower().replace("ё", "е").replace("\xa0", " ")
    text = re.sub(r"[«»\"'`()\[\]{}:;,.!?/\\|+=_*<>-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_price(value):
    if pd.isna(value):
        return None

    text = str(value).strip()
    if not text:
        return None

    lowered = text.lower().replace("\xa0", " ")
    if lowered in {"", "nan", "none", "по запросу", "позапросу"}:
        return None

    cleaned = (
        lowered
        .replace("от ", "")
        .replace("₽", "")
        .replace("руб.", "")
        .replace("руб", "")
        .replace(",", ".")
        .replace(" ", "")
    )

    try:
        price = float(cleaned)
        return price if price > 0 else None
    except Exception:
        return None


def extract_budget(text):
    text = text.lower().replace("\xa0", " ").strip()

    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(к|тыс|тысяч)", text)
    if m:
        value = float(m.group(1).replace(",", "."))
        return int(value * 1000)

    compact = text.replace(" ", "")
    m2 = re.search(r"(\d{3,6})", compact)
    if m2:
        return int(m2.group(1))

    return None


def detect_vip(text):
    vip_words = [
        "лукойл", "газпром", "роснефть", "сбер", "сбербанк",
        "топ-менедж", "топ менедж", "vip", "вип",
        "директор", "руковод", "гендир", "генеральн", "ceo",
        "президент", "акционер", "премиум", "статус"
    ]
    text = text.lower()
    return any(word in text for word in vip_words)


def detect_gender(text):
    text = text.lower()

    female_words = ["жене", "женщин", "девушк", "маме", "подруге", "сестре", "дочке", "дочер", "бабушке"]
    male_words = ["мужу", "мужчин", "парню", "папе", "другу", "брату", "сыну", "дедушке"]

    female = any(word in text for word in female_words)
    male = any(word in text for word in male_words)

    if female and not male:
        return "female"
    if male and not female:
        return "male"
    return "neutral"


def detect_query_type(text):
    text_lower = text.lower()

    selection_words = [
        "подбери", "подбор", "подборка", "подарок", "подарки",
        "посоветуй", "что подарить", "варианты", "сделай подборку",
        "нужен подарок", "ищу подарок", "хочу подарить"
    ]

    question_words = [
        "как", "что", "где", "когда", "почему", "зачем",
        "какой", "какие", "можно ли", "что такое",
        "чем отличается", "есть ли", "нужно ли"
    ]

    if any(word in text_lower for word in selection_words):
        return "selection"

    if "?" in text_lower or any(word in text_lower for word in question_words):
        return "question"

    if extract_budget(text_lower):
        return "selection"

    return "question"


# =========================
# FAQ
# =========================

def load_faq_entries(path=FAQ_FILE):
    if not os.path.exists(path):
        print(f"[FAQ] Файл не найден: {path}")
        return [], ""

    try:
        with open(path, "r", encoding="utf-8") as f:
            faq_text = f.read()

        pattern = r"Вопрос:\s*(.*?)\s*Ответ:\s*(.*?)(?=\n\s*Вопрос:|\Z)"
        matches = re.findall(pattern, faq_text, flags=re.S)

        entries = []
        for question, answer in matches:
            q = question.strip()
            a = answer.strip()
            if q and a:
                entries.append({
                    "question": q,
                    "answer": a,
                    "question_norm": normalize_text(q)
                })

        print(f"[FAQ] Загружено вопросов: {len(entries)}")
        return entries, faq_text

    except Exception as e:
        print(f"[FAQ] Ошибка чтения FAQ: {e}")
        return [], ""


FAQ_ENTRIES, FAQ_RAW_TEXT = load_faq_entries()


def find_local_faq_answer(user_message):
    if not FAQ_ENTRIES:
        return None

    query_norm = normalize_text(user_message)
    if not query_norm:
        return None

    query_tokens = set(query_norm.split())

    best_score = 0
    best_answer = None

    for item in FAQ_ENTRIES:
        q_norm = item["question_norm"]
        q_tokens = set(q_norm.split())

        # коэффициент пересечения слов
        overlap = len(query_tokens & q_tokens) / max(1, len(query_tokens))

        # коэффициент похожести строк
        similarity = SequenceMatcher(None, query_norm, q_norm).ratio()

        # итоговый скор
        score = similarity * 0.5 + overlap * 0.5

        if score > best_score:
            best_score = score
            best_answer = item["answer"]

    # понижаем порог
    if best_score >= 0.55:
        return best_answer

    return None


def answer_question(user_message):
    local_answer = find_local_faq_answer(user_message)
    if local_answer:
        return local_answer

    if client and FAQ_RAW_TEXT:
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты консультант gifts.ru.\n"
                            "Отвечай только на основе базы ниже.\n"
                            "Если точного ответа в базе нет, ответь строго так: "
                            f"\"{FALLBACK_FAQ_REPLY}\"\n"
                            "Не используй звездочки.\n"
                            "Пиши кратко и по делу.\n\n"
                            f"БАЗА:\n{FAQ_RAW_TEXT}"
                        )
                    },
                    {"role": "user", "content": user_message}
                ],
                temperature=0,
                max_tokens=400
            )
            return response.choices[0].message.content.strip().replace("*", "")
        except Exception as e:
            print(f"[OpenAI] Ошибка: {e}")

    return FALLBACK_FAQ_REPLY


# =========================
# КАТАЛОГ И ЦЕНЫ
# =========================

def empty_catalog():
    columns = ["Название", "Артикул", "Категория", "Цена", "Фото", "Цена_число", "Категория_норм"]
    return pd.DataFrame(columns=columns)


def normalize_category(name, category):
    text = f"{name} {category}".lower()

    rules = {
        "Подарочные наборы": ["набор", "set"],
        "Электроника": ["аккумулятор", "power", "колонка", "заряд", "фонарик", "usb", "type-c", "гаджет"],
        "Посуда": ["круж", "термос", "бутыл", "чайник", "стакан"],
        "Сумки и аксессуары": ["сумка", "рюкзак", "несессер", "портмоне", "кошелек"],
        "Текстиль и одежда": ["плед", "футбол", "жилет", "дождевик", "шапк", "рубашк", "куртк", "толстовк"],
        "Офис и канцелярия": ["ежедневник", "ручка", "блокнот", "органайзер", "папка"],
        "Дом и интерьер": ["лампа", "светильник", "интерьер", "дом"],
        "Награды": ["награ", "стела", "кубок", "приз"],
        "Красота и здоровье": ["уход", "массаж", "здоров"],
        "Отдых и туризм": ["термосумка", "поход", "туризм", "отдых"]
    }

    for cat_name, words in rules.items():
        if any(word in text for word in words):
            return cat_name

    if category and str(category).strip().lower() != "nan":
        return str(category).replace("_", " ").strip().capitalize()

    return "Без категории"


def load_price_file(price_file=PRICE_FILE):
    if not os.path.exists(price_file):
        print(f"[PRICE] Файл с ценами не найден: {price_file}")
        return None

    try:
        prices = pd.read_excel(price_file)

        if "Артикул" not in prices.columns or "Цена" not in prices.columns:
            print(f"[PRICE] В {price_file} нужны колонки 'Артикул' и 'Цена'")
            return None

        prices = prices[["Артикул", "Цена"]].copy()
        prices["Артикул"] = prices["Артикул"].astype(str).str.strip()
        prices["Цена"] = prices["Цена"].astype(str).str.strip()
        prices = prices.drop_duplicates(subset=["Артикул"])

        print(f"[PRICE] Загружено цен: {len(prices)}")
        return prices

    except Exception as e:
        print(f"[PRICE] Ошибка чтения файла с ценами: {e}")
        return None


def load_catalog(catalog_file=CATALOG_FILE):
    if not os.path.exists(catalog_file):
        print(f"[CATALOG] Файл каталога не найден: {catalog_file}")
        return empty_catalog()

    try:
        df = pd.read_excel(catalog_file)
    except Exception as e:
        print(f"[CATALOG] Ошибка чтения каталога: {e}")
        return empty_catalog()

    for col in ["Название", "Артикул", "Категория", "Цена", "Фото"]:
        if col not in df.columns:
            df[col] = ""

    df = df[["Название", "Артикул", "Категория", "Цена", "Фото"]].copy()

    df["Название"] = df["Название"].astype(str).str.strip()
    df["Артикул"] = df["Артикул"].astype(str).str.strip()
    df["Категория"] = df["Категория"].fillna("").astype(str).str.strip()
    df["Цена"] = df["Цена"].fillna("").astype(str).str.strip()
    df["Фото"] = df["Фото"].fillna("").astype(str).str.strip()

    # Если позже появится отдельный prices.xlsx,
    # он автоматически подмешается по колонке "Артикул".
    prices_df = load_price_file()
    if prices_df is not None and not prices_df.empty:
        df = df.merge(
            prices_df.rename(columns={"Цена": "Цена_из_файла"}),
            on="Артикул",
            how="left"
        )

        empty_price_mask = df["Цена"].apply(lambda x: parse_price(x) is None)
        df.loc[empty_price_mask, "Цена"] = df.loc[empty_price_mask, "Цена_из_файла"].fillna("")
        df = df.drop(columns=["Цена_из_файла"])

    df = df[
        (df["Название"].str.len() > 2) &
        (~df["Название"].str.contains(
            "Бренд|Размер|Свободно|На складе|В пути|Европа|Поиск|Найдено",
            case=False, na=False
        ))
    ].copy()

    df["Цена_число"] = df["Цена"].apply(parse_price)

    df = df.drop_duplicates(subset=["Название", "Артикул"]).reset_index(drop=True)

    if df.empty:
        df["Категория_норм"] = []
    else:
        df["Категория_норм"] = df.apply(
            lambda row: normalize_category(row["Название"], row["Категория"]),
            axis=1
        )

    return df


df = load_catalog()
df_priced = df[df["Цена_число"].notna()].copy()
PRICE_DATA_AVAILABLE = not df_priced.empty

print("=" * 60)
print(f"Модель ИИ: {MODEL_NAME}")
print(f"Каталог: {CATALOG_FILE}")
print(f"Файл цен: {PRICE_FILE}")
print(f"Всего товаров в каталоге: {len(df)}")
print(f"Товаров с ценами: {len(df_priced)}")
print(f"Подборки по бюджету доступны: {PRICE_DATA_AVAILABLE}")
print("=" * 60)


# =========================
# ПОДБОРКА ТОВАРОВ
# =========================

def build_selection(budget, is_vip, gender):
    if not PRICE_DATA_AVAILABLE:
        return None

    available = df_priced[df_priced["Цена_число"] <= budget].copy()
    if available.empty:
        return pd.DataFrame()

    if is_vip:
        available = available[available["Цена_число"] >= budget * 0.2]
        bad_words = [
            "брелок", "кружка", "стакан", "футболка", "шоппер",
            "значок", "карандаш", "открывалка", "зажигалка",
            "чехол", "коврик", "шнурок", "салфетка", "пакет",
            "антистресс", "носки"
        ]
        pattern = "|".join(bad_words)
        available = available[~available["Название"].str.contains(pattern, case=False, na=False)]
    else:
        available = available[available["Цена_число"] >= max(30, budget * 0.05)]

    if gender == "female":
        available = available[~available["Название"].str.contains("мужск|бритв|жилет|дождевик", case=False, na=False)]
    elif gender == "male":
        available = available[~available["Название"].str.contains("женск|косметичк|ваза", case=False, na=False)]

    if available.empty:
        return pd.DataFrame()

    available["delta"] = (budget - available["Цена_число"]).abs()
    available = available.sort_values(by=["delta", "Цена_число"], ascending=[True, False])

    result_rows = []
    used_names = set()
    category_counts = {}

    for _, row in available.iterrows():
        name = row["Название"]
        cat = row["Категория_норм"]

        if name in used_names:
            continue

        if category_counts.get(cat, 0) < 1:
            result_rows.append(row)
            used_names.add(name)
            category_counts[cat] = category_counts.get(cat, 0) + 1

        if len(result_rows) == 5:
            break

    if len(result_rows) < 5:
        for _, row in available.iterrows():
            name = row["Название"]
            cat = row["Категория_норм"]

            if name in used_names:
                continue

            if category_counts.get(cat, 0) < 2:
                result_rows.append(row)
                used_names.add(name)
                category_counts[cat] = category_counts.get(cat, 0) + 1

            if len(result_rows) == 5:
                break

    return pd.DataFrame(result_rows).head(5)


def format_products_reply(products, is_vip):
    intro = "Подобрал 5 вариантов в указанном бюджете."
    if is_vip:
        intro = "Подобрал 5 статусных вариантов в указанном бюджете."

    lines = [intro, ""]

    for i, (_, p) in enumerate(products.iterrows(), start=1):
        price = int(p["Цена_число"]) if float(p["Цена_число"]).is_integer() else p["Цена_число"]

        lines.append(f"{i}. {p['Название']}")
        lines.append(f"Категория: {p['Категория_норм']}")
        lines.append(f"Цена: {price} руб.")
        lines.append(f"Артикул: {p['Артикул']}")

        if pd.notna(p.get("Фото")) and str(p.get("Фото")).strip() and str(p.get("Фото")) != "nan":
            lines.append(f"Фото: {p['Фото']}")

        lines.append("")

    if len(products) < 5:
        lines.append(f"В текущей базе нашлось {len(products)} подходящих вариантов.")

    return "\n".join(lines).strip()


# =========================
# ROUTES
# =========================

@app.route("/")
def index():
    try:
        return render_template("index.html")
    except TemplateNotFound:
        return "Бот gifts.ru запущен. Используйте POST /chat"


@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = str(data.get("message", "")).strip()

        if not user_message:
            return jsonify({"reply": "Напишите запрос, и я помогу."})

        query_type = detect_query_type(user_message)

        if query_type == "selection":
            if not PRICE_DATA_AVAILABLE:
                return jsonify({
                    "reply": (
                        "Сейчас я могу отвечать на вопросы по gifts.ru, "
                        "но подборки товаров временно недоступны: в каталоге нет цен. "
                        "Когда появится файл prices.xlsx с колонками «Артикул» и «Цена», "
                        "подборки заработают автоматически."
                    )
                })

            budget = extract_budget(user_message)
            if not budget:
                return jsonify({"reply": "Укажите, пожалуйста, бюджет, и я подберу варианты."})

            is_vip = detect_vip(user_message)
            gender = detect_gender(user_message)

            products = build_selection(budget, is_vip, gender)

            if products is None or products.empty:
                return jsonify({
                    "reply": "К сожалению, в указанном бюджете подходящих товаров не найдено. Попробуйте увеличить бюджет или уточнить запрос."
                })

            return jsonify({"reply": format_products_reply(products, is_vip)})

        return jsonify({"reply": answer_question(user_message)})

    except Exception as e:
        print(f"[APP] Ошибка: {e}")
        return jsonify({"reply": "Произошла ошибка. Попробуйте еще раз."})


if __name__ == "__main__":
    print("СЕРВЕР ЗАПУЩЕН")
    app.run(port=5000)