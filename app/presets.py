"""Built-in activity decks and curated labels; seeded only once, never overwrites user edits."""
import json

LANGUAGES = {
 'tr':'Türkçe','en':'English','ru':'Русский','de':'Deutsch','fr':'Français',
 'es':'Español','it':'Italiano','pt':'Português','nl':'Nederlands','pl':'Polski',
 'uk':'Українська','az':'Azərbaycanca','ar':'العربية','ja':'日本語','ko':'한국어','zh':'简体中文'
}
# One value per activity, in the same order as deck definitions below.
DECKS = [
 ('Classic', ['food','coffee','bar','party','gaming','surprise'], ['🍔','☕','🍺','🎉🍻','👾','🥰']),
 ('Adventure', ['drive','walk','roadtrip','bowling','picnic','surprise'], ['🚗','🌃','🗺️','🎳','🧺','🥰']),
 ('Movies & Chill', ['movies','coffee','dinner','gaming','concert','surprise'], ['🍿','☕','🍝','👾','🎶','🥰'])
]
# Each language provides all 18 labels (3 decks × 6), including repeated activities.
TRANSLATIONS = {
 'tr': ['Bir şeyler yiyelim','Kahvelenelim','Hoş müzik & Bar','Partileyelim','Oyun oynayalım','Farketmez, akışına bırakalım', 'Arabayla gezelim','Gece yürüyüşü','Küçük bir yolculuk','Bowling oynayalım','Piknik yapalım','Farketmez, akışına bırakalım', 'Film izleyelim','Kahvelenelim','Akşam yemeği','Oyun oynayalım','Canlı müzik dinleyelim','Farketmez, akışına bırakalım'],
 'en': ["Let's grab a bite","Let's get coffee","Music & bar","Let's party","Let's play games","Let's go with the flow", "Let's go for a drive","Night walk","Mini road trip","Let's go bowling","Let's have a picnic","Let's go with the flow", "Watch a movie","Let's get coffee","Dinner together","Let's play games","Live music","Let's go with the flow"],
 'ru': ['Давай перекусим','Выпьем кофе','Музыка и бар','Потусуемся','Поиграем','Давай решим на месте', 'Покатаемся на машине','Ночная прогулка','Небольшое путешествие','Поиграем в боулинг','Устроим пикник','Давай решим на месте', 'Посмотрим фильм','Выпьем кофе','Поужинаем','Поиграем','Живая музыка','Давай решим на месте'],
 'de': ['Etwas essen gehen','Einen Kaffee trinken','Musik & Bar','Feiern gehen','Spiele spielen','Mal sehen, worauf wir Lust haben', 'Eine Runde fahren','Nachtspaziergang','Kleiner Roadtrip','Bowling spielen','Picknick machen','Mal sehen, worauf wir Lust haben', 'Einen Film schauen','Einen Kaffee trinken','Gemeinsam essen','Spiele spielen','Livemusik','Mal sehen, worauf wir Lust haben'],
 'fr': ['Allons manger','Prendre un café','Musique et bar','Faire la fête','Jouer ensemble','On verra sur le moment', 'Faire un tour en voiture','Balade nocturne','Petite escapade','Faire du bowling','Faire un pique-nique','On verra sur le moment', 'Regarder un film','Prendre un café','Dîner ensemble','Jouer ensemble','Musique live','On verra sur le moment'],
 'es': ['Vamos a comer','Tomemos un café','Música y bar','Vamos de fiesta','Juguemos','Ya veremos qué nos apetece', 'Dar una vuelta en coche','Paseo nocturno','Viaje corto','Jugar a los bolos','Hacer un pícnic','Ya veremos qué nos apetece', 'Ver una película','Tomemos un café','Cenar juntos','Juguemos','Música en vivo','Ya veremos qué nos apetece'],
 'it': ['Andiamo a mangiare','Prendiamo un caffè','Musica e bar','Facciamo festa','Giochiamo','Vediamo cosa ci va di fare', 'Un giro in macchina','Passeggiata serale','Piccolo viaggio','Andiamo a bowling','Facciamo un picnic','Vediamo cosa ci va di fare', 'Guardiamo un film','Prendiamo un caffè','Cena insieme','Giochiamo','Musica dal vivo','Vediamo cosa ci va di fare'],
 'pt': ['Vamos comer','Vamos tomar café','Música e bar','Vamos festejar','Vamos jogar','Vamos decidir na hora', 'Vamos dar uma volta de carro','Passeio noturno','Viagem curta','Vamos jogar boliche','Vamos fazer piquenique','Vamos decidir na hora', 'Assistir a um filme','Vamos tomar café','Jantar juntos','Vamos jogar','Música ao vivo','Vamos decidir na hora'],
 'nl': ['Iets gaan eten','Koffie drinken','Muziek en bar','Een feestje bouwen','Spelletjes spelen','We zien wel waar we zin in hebben', 'Een stukje rijden','Avondwandeling','Kleine roadtrip','Bowlen','Picknicken','We zien wel waar we zin in hebben', 'Een film kijken','Koffie drinken','Samen uit eten','Spelletjes spelen','Live muziek','We zien wel waar we zin in hebben'],
 'pl': ['Chodźmy coś zjeść','Chodźmy na kawę','Muzyka i bar','Idziemy na imprezę','Pograjmy','Zdecydujemy na miejscu', 'Przejażdżka autem','Nocny spacer','Krótka wycieczka','Idziemy na kręgle','Zróbmy piknik','Zdecydujemy na miejscu', 'Obejrzyjmy film','Chodźmy na kawę','Wspólna kolacja','Pograjmy','Muzyka na żywo','Zdecydujemy na miejscu'],
 'uk': ['Ходімо поїмо','Вип’ємо кави','Музика і бар','Підемо на вечірку','Пограймо','Вирішимо разом на місці', 'Поїздка на авто','Нічна прогулянка','Невелика подорож','Пограймо в боулінг','Влаштуймо пікнік','Вирішимо разом на місці', 'Подивімося фільм','Вип’ємо кави','Вечеря разом','Пограймо','Жива музика','Вирішимо разом на місці'],
 'az': ['Bir şeylər yeyək','Qəhvə içək','Musiqi və bar','Əylənək','Oyun oynayaq','Axarına buraxaq', 'Maşınla gəzək','Gecə gəzintisi','Kiçik səyahət','Boulinq oynayaq','Piknik edək','Axarına buraxaq', 'Film izləyək','Qəhvə içək','Birlikdə şam yeməyi','Oyun oynayaq','Canlı musiqi','Axarına buraxaq'],
 'ar': ['لنأكل شيئًا','لنشرب القهوة','موسيقى ومقهى','لنحتفل','لنلعب','نقرر معًا وقتها', 'جولة بالسيارة','نزهة ليلية','رحلة قصيرة','لنلعب البولينغ','لنقم بنزهة','نقرر معًا وقتها', 'لنشاهد فيلمًا','لنشرب القهوة','عشاء معًا','لنلعب','موسيقى حية','نقرر معًا وقتها'],
 'ja': ['何か食べよう','コーヒーを飲もう','音楽とバー','パーティーしよう','ゲームしよう','そのとき一緒に決めよう', 'ドライブしよう','夜のお散歩','小旅行','ボウリングしよう','ピクニックしよう','そのとき一緒に決めよう', '映画を観よう','コーヒーを飲もう','一緒にディナー','ゲームしよう','生演奏を聴こう','そのとき一緒に決めよう'],
 'ko': ['맛있는 거 먹자','커피 마시자','음악과 바','파티하자','게임하자','그때 같이 정하자', '드라이브하자','밤 산책','짧은 여행','볼링 치자','피크닉 가자','그때 같이 정하자', '영화 보자','커피 마시자','함께 저녁 먹자','게임하자','라이브 음악','그때 같이 정하자'],
 'zh': ['一起吃点东西','一起喝咖啡','音乐与酒吧','一起去派对','一起玩游戏','到时候一起决定吧', '一起兜风','夜间散步','短途旅行','一起打保龄球','一起野餐','到时候一起决定吧', '一起看电影','一起喝咖啡','共进晚餐','一起玩游戏','现场音乐','到时候一起决定吧'],
}
assert all(len(v)==18 for v in TRANSLATIONS.values())

def seed(db):
    if db.execute('SELECT 1 FROM decks LIMIT 1').fetchone():return
    n=0
    for deck_idx,(name,slugs,emojis) in enumerate(DECKS):
        db.execute('INSERT INTO decks(name,archived,created_at) VALUES(?,0,datetime("now"))',(name,))
        deck_id=db.execute('SELECT last_insert_rowid()').fetchone()[0]
        for pos,(slug,emoji) in enumerate(zip(slugs,emojis)):
            translations={lang:labels[n] for lang,labels in TRANSLATIONS.items()}
            db.execute('INSERT INTO deck_cards(deck_id,slug,emoji,translations,position) VALUES(?,?,?,?,?)',
                       (deck_id,slug,emoji,json.dumps(translations,ensure_ascii=False),pos))
            n+=1
    db.execute('INSERT OR IGNORE INTO config(key,value) VALUES(?,?)',('guest_deck_id','1'))
    db.execute('INSERT OR IGNORE INTO config(key,value) VALUES(?,?)',('guest_language','en'))
    db.execute('INSERT OR IGNORE INTO config(key,value) VALUES(?,?)',('admin_language','en'))
