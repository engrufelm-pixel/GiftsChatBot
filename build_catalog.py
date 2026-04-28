import os
import re
import pandas as pd

txt_files = [
    "novinki.txt",
    "odejda.txt",
    "posuda.txt",
    "ruchki.txt",
    "sumki.txt",
    "zonty.txt",
    "elektronika.txt",
    "podarochnye_nabory.txt",
    "korporativnye_podarki.txt",
]

all_products = []

for file in txt_files:
    if not os.path.exists(file):
        print(f"⚠ {file} не найден")
        continue

    print(f"Обрабатываю {file}...")

    with open(file, "r", encoding="utf-8") as f:
        text = f.read()

    category = file.replace(".txt", "")

    pattern = re.findall(
        r"([А-ЯA-ZЁ].+?)\nАртикул:\s*([\d\.A-Za-z]+)(.*?)(?=\n[А-ЯA-ZЁ].+?\nАртикул:|\Z)",
        text,
        re.DOTALL
    )

    for match in pattern:
        name_block = match[0].strip()
        article = match[1].strip()
        block = match[2]

        price_match = re.search(r"(\d[\d\s]*,\d+|\d[\d\s]*)", name_block)
        price = price_match.group(1).replace(" ", "") if price_match else ""

        brand_match = re.search(r"Бренд:\s*(.+)", block)
        brand = brand_match.group(1).strip() if brand_match else ""

        size_match = re.search(r"Размеры:\s*(.+)", block)
        size = size_match.group(1).strip() if size_match else ""

        stock_match = re.search(r"На складе:\s*([\d\s]+)", block)
        stock = stock_match.group(1).replace(" ", "") if stock_match else ""

        free_match = re.search(r"Свободно:\s*([\d\s]+)", block)
        free = free_match.group(1).replace(" ", "") if free_match else ""

        way_match = re.search(r"В пути:\s*([\d\s]+)", block)
        way = way_match.group(1).replace(" ", "") if way_match else ""

        all_products.append({
            "Название": name_block.split("\n")[0],
            "Цена": price,
            "Артикул": article,
            "Бренд": brand,
            "Размеры": size,
            "На складе": stock,
            "Свободно": free,
            "В пути": way,
            "Категория": category
        })

df = pd.DataFrame(all_products)
df.drop_duplicates(subset=["Артикул"], inplace=True)
df.to_excel("catalog.xlsx", index=False)

print("✅ Готово!")
print("Всего товаров:", len(df))