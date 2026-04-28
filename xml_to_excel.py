import pandas as pd
import xml.etree.ElementTree as ET

def convert_client_xml():
    print("Начинаю читать огромный файл клиента...")
    tree = ET.parse('product.xml')
    root = tree.getroot()
    
    data = []
    # Проходим по всем товарам
    for p in root.findall('.//product'):
        name = p.findtext('name')
        article = p.findtext('code') # У них артикул записан в теге code
        cat = p.findtext('groupname')
        
        # Пытаемся найти цену (если она есть в этом файле)
        price = p.findtext('price')
        if not price:
            price = p.findtext('price_rub') # Иногда так
            
        # Берем фото!
        img_tag = p.find('super_big_image')
        img_url = ""
        if img_tag is not None:
            # У gifts.ru картинки обычно лежат на поддомене files.gifts.ru или api.gifts.ru
            img_url = f"https://api.gifts.ru/{img_tag.get('src')}"
            
        if name:
            data.append({
                'Название': name,
                'Цена': price if price else "По запросу",
                'Артикул': article,
                'Категория': cat,
                'Фото': img_url
            })
            
    df = pd.DataFrame(data)
    # Сохраняем в Excel
    df.to_excel('catalog.xlsx', index=False)
    print(f"ГОТОВО! Я переварил {len(df)} товаров и создал catalog.xlsx")

if __name__ == "__main__":
    convert_client_xml()