"""
Czech city geocoding module for government auction listings.

Provides a static dictionary mapping Czech city names to (latitude, longitude)
coordinates, primarily for geocoding the `Mesto_prevzeti` field from the Czech
Financial Administration's auction system at drazby.fs.gov.cz.

Both diacritics-free (ASCII) and diacritics variants are included as keys.
"""

import re
from typing import Optional

# =============================================================================
# CZECH_CITIES: Mapping of city name -> (latitude, longitude)
#
# Organization:
#   1. Regional capitals (krajska mesta) - 13 entries
#   2. District capitals (okresni mesta) - 76 districts + Praha
#   3. Other notable cities and towns
#
# Each city appears with its ASCII name as the primary key.
# Diacritics variants are added at the end of this file.
# =============================================================================

CZECH_CITIES: dict[str, tuple[float, float]] = {

    # =========================================================================
    # 1. REGIONAL CAPITALS (Krajska mesta)
    # =========================================================================

    # Praha - Capital of Czech Republic & Prague Region
    "Praha": (50.0755, 14.4378),

    # Brno - Capital of South Moravian Region (Jihomoravsky kraj)
    "Brno": (49.1951, 16.6068),

    # Ostrava - Capital of Moravian-Silesian Region (Moravskoslezsky kraj)
    "Ostrava": (49.8209, 18.2625),

    # Plzen - Capital of Plzen Region (Plzensky kraj)
    "Plzen": (49.7384, 13.3736),

    # Liberec - Capital of Liberec Region (Liberecky kraj)
    "Liberec": (50.7663, 15.0543),

    # Olomouc - Capital of Olomouc Region (Olomoucky kraj)
    "Olomouc": (49.5938, 17.2509),

    # Ceske Budejovice - Capital of South Bohemian Region (Jihocesky kraj)
    "Ceske Budejovice": (48.9745, 14.4747),

    # Hradec Kralove - Capital of Hradec Kralove Region (Kralovehradecky kraj)
    "Hradec Kralove": (50.2104, 15.8253),

    # Usti nad Labem - Capital of Usti nad Labem Region (Ustecky kraj)
    "Usti nad Labem": (50.6607, 14.0323),

    # Pardubice - Capital of Pardubice Region (Pardubicky kraj)
    "Pardubice": (50.0343, 15.7812),

    # Zlin - Capital of Zlin Region (Zlinsky kraj)
    "Zlin": (49.2267, 17.6672),

    # Jihlava - Capital of Vysocina Region (Kraj Vysocina)
    "Jihlava": (49.3961, 15.5912),

    # Karlovy Vary - Capital of Karlovy Vary Region (Karlovarsky kraj)
    "Karlovy Vary": (50.2325, 12.8714),

    # =========================================================================
    # 2. DISTRICT CAPITALS (Okresni mesta)
    #    Grouped by region. Regional capitals listed above are not repeated here.
    # =========================================================================

    # --- Central Bohemian Region (Stredocesky kraj) ---
    "Benesov": (49.7818, 14.6869),
    "Beroun": (49.9639, 14.0722),
    "Kladno": (50.1473, 14.1067),
    "Kolin": (50.0283, 15.1998),
    "Kutna Hora": (49.9481, 15.2681),
    "Melnik": (50.3506, 14.4742),
    "Mlada Boleslav": (50.4112, 14.9063),
    "Nymburk": (50.1862, 15.0418),
    "Praha-vychod": (50.0755, 14.4378),   # Administrative district, uses Praha coords
    "Praha-zapad": (50.0755, 14.4378),    # Administrative district, uses Praha coords
    "Pribram": (49.6893, 14.0101),
    "Rakovnik": (50.1046, 13.7335),

    # --- South Bohemian Region (Jihocesky kraj) ---
    # Ceske Budejovice listed above as regional capital
    "Cesky Krumlov": (48.8127, 14.3175),
    "Jindrichuv Hradec": (49.1441, 15.0027),
    "Pisek": (49.3088, 14.1475),
    "Prachatice": (49.0125, 13.9974),
    "Strakonice": (49.2613, 13.9024),
    "Tabor": (49.4147, 14.6578),

    # --- Plzen Region (Plzensky kraj) ---
    # Plzen listed above as regional capital
    "Domazlice": (49.4407, 12.9296),
    "Klatovy": (49.3955, 13.2952),
    "Plzen-mesto": (49.7384, 13.3736),    # Same as Plzen
    "Plzen-jih": (49.7384, 13.3736),      # Administrative district, uses Plzen coords
    "Plzen-sever": (49.7384, 13.3736),    # Administrative district, uses Plzen coords
    "Rokycany": (49.7428, 13.5946),
    "Tachov": (49.7953, 12.6336),

    # --- Karlovy Vary Region (Karlovarsky kraj) ---
    # Karlovy Vary listed above as regional capital
    "Cheb": (50.0796, 12.3714),
    "Sokolov": (50.1814, 12.6401),

    # --- Usti nad Labem Region (Ustecky kraj) ---
    # Usti nad Labem listed above as regional capital
    "Decin": (50.7814, 14.2148),
    "Chomutov": (50.4606, 13.4175),
    "Litomerice": (50.5336, 14.1318),
    "Louny": (50.3564, 13.7960),
    "Most": (50.5031, 13.6367),
    "Teplice": (50.6405, 13.8245),

    # --- Liberec Region (Liberecky kraj) ---
    # Liberec listed above as regional capital
    "Ceska Lipa": (50.6858, 14.5378),
    "Jablonec nad Nisou": (50.7274, 15.1710),
    "Semily": (50.6020, 15.3343),

    # --- Hradec Kralove Region (Kralovehradecky kraj) ---
    # Hradec Kralove listed above as regional capital
    "Jicin": (50.4371, 15.3519),
    "Nachod": (50.4167, 16.1628),
    "Rychnov nad Kneznou": (50.1630, 16.2748),
    "Trutnov": (50.5610, 15.9127),

    # --- Pardubice Region (Pardubicky kraj) ---
    # Pardubice listed above as regional capital
    "Chrudim": (49.9510, 15.7951),
    "Svitavy": (49.7555, 16.4685),
    "Usti nad Orlici": (49.9738, 16.3934),

    # --- Vysocina Region (Kraj Vysocina) ---
    # Jihlava listed above as regional capital
    "Havlickuv Brod": (49.6067, 15.5808),
    "Pelhrimov": (49.4314, 15.2232),
    "Trebic": (49.2148, 15.8817),
    "Zdar nad Sazavou": (49.5627, 15.9393),

    # --- South Moravian Region (Jihomoravsky kraj) ---
    # Brno listed above as regional capital
    "Blansko": (49.3631, 16.6444),
    "Breclav": (48.7590, 16.8820),
    "Brno-mesto": (49.1951, 16.6068),     # Same as Brno
    "Brno-venkov": (49.1951, 16.6068),    # Administrative district, uses Brno coords
    "Hodonin": (48.8494, 17.1326),
    "Vyskov": (49.2776, 16.9991),
    "Znojmo": (48.8555, 16.0488),

    # --- Olomouc Region (Olomoucky kraj) ---
    # Olomouc listed above as regional capital
    "Jesenik": (50.2293, 17.2046),
    "Prostejov": (49.4718, 17.1118),
    "Prerov": (49.4552, 17.4510),
    "Sumperk": (49.9656, 16.9706),

    # --- Zlin Region (Zlinsky kraj) ---
    # Zlin listed above as regional capital
    "Kromeriz": (49.2976, 17.3935),
    "Uherske Hradiste": (49.0698, 17.4597),
    "Vsetin": (49.3388, 17.9960),

    # --- Moravian-Silesian Region (Moravskoslezsky kraj) ---
    # Ostrava listed above as regional capital
    "Bruntal": (49.9884, 17.4647),
    "Frydek-Mistek": (49.6882, 18.3537),
    "Karvina": (49.8541, 18.5428),
    "Novy Jicin": (49.5941, 18.0103),
    "Opava": (49.9381, 17.9045),
    "Ostrava-mesto": (49.8209, 18.2625),  # Same as Ostrava

    # =========================================================================
    # 3. OTHER NOTABLE CITIES AND TOWNS
    # =========================================================================

    # Major cities not already listed
    "Havirov": (49.7799, 18.4371),
    "Trinec": (49.6774, 18.6725),
    "Karvina": (49.8541, 18.5428),       # Also a district capital (duplicate safe)

    # --- Additional cities in Central Bohemian Region ---
    "Brandys nad Labem-Stara Boleslav": (50.1867, 14.6600),
    "Brandys nad Labem": (50.1867, 14.6600),
    "Kralupy nad Vltavou": (50.2408, 14.3114),
    "Ricany": (49.9920, 14.6561),
    "Sedlcany": (49.6607, 14.4268),
    "Slany": (50.2306, 14.0869),
    "Vlasim": (49.7068, 14.8985),
    "Votice": (49.6403, 14.6379),
    "Mnichovo Hradiste": (50.5275, 15.0063),
    "Neratovice": (50.2591, 14.5176),
    "Lysa nad Labem": (50.2014, 14.8333),
    "Podebrady": (50.1424, 15.1188),
    "Cesky Brod": (50.0746, 14.8603),
    "Revnice": (49.9140, 14.2294),
    "Cernosice": (49.9596, 14.3198),
    "Hostivice": (50.0814, 14.2581),
    "Roztoky": (50.1541, 14.3960),
    "Dobris": (49.7810, 14.1674),
    "Nove Straseci": (50.1528, 13.9008),

    # --- Additional cities in South Bohemian Region ---
    "Trebon": (49.0036, 14.7706),
    "Vimperk": (49.0585, 13.7849),
    "Milevsko": (49.4497, 14.3600),
    "Blatna": (49.4254, 13.8810),
    "Veseli nad Luznici": (49.1843, 14.6977),
    "Sobeslav": (49.2590, 14.7181),
    "Dacice": (49.0813, 15.4376),
    "Tyn nad Vltavou": (49.2224, 14.4206),
    "Vodnany": (49.1484, 14.1748),

    # --- Additional cities in Plzen Region ---
    "Susice": (49.2315, 13.5170),
    "Horazovice": (49.3213, 13.7064),
    "Nepomuk": (49.4872, 13.5794),
    "Stribro": (49.7562, 12.9968),
    "Horsovsky Tyn": (49.5261, 12.9423),
    "Kralovice": (49.9794, 13.4873),
    "Nyrsko": (49.2923, 13.1541),
    "Plasy": (49.9340, 13.3921),

    # --- Additional cities in Karlovy Vary Region ---
    "Marianske Lazne": (49.9646, 12.7013),
    "Ostrov": (50.3063, 12.9389),
    "Frantiskovy Lazne": (50.1204, 12.3516),
    "Jachymov": (50.3712, 12.9057),
    "Nejdek": (50.3234, 12.7299),
    "As": (50.2239, 12.1947),
    "Kraslice": (50.3235, 12.5178),

    # --- Additional cities in Usti nad Labem Region ---
    "Bilina": (50.5484, 13.7749),
    "Duchcov": (50.6038, 13.7463),
    "Kadan": (50.3828, 13.2710),
    "Litvinov": (50.6008, 13.6113),
    "Rumburk": (50.9517, 14.5569),
    "Varnsdorf": (50.9115, 14.6189),
    "Roudnice nad Labem": (50.4253, 14.2610),
    "Lovosice": (50.5151, 14.0513),
    "Zatec": (50.3276, 13.5462),
    "Jirkov": (50.4999, 13.4474),
    "Krupka": (50.6849, 13.8595),
    "Usti nad Labem-mesto": (50.6607, 14.0323),
    "Podborany": (50.2283, 13.4152),

    # --- Additional cities in Liberec Region ---
    "Turnov": (50.5874, 15.1543),
    "Tanvald": (50.7367, 15.3071),
    "Zelezny Brod": (50.6431, 15.2539),
    "Novy Bor": (50.7575, 14.5560),
    "Mimon": (50.6591, 14.7260),
    "Frydlant": (50.9213, 15.0789),
    "Doksy": (50.5596, 14.6551),
    "Jilemnice": (50.6092, 15.5067),

    # --- Additional cities in Hradec Kralove Region ---
    "Dvur Kralove nad Labem": (50.4316, 15.8122),
    "Broumov": (50.5861, 16.3325),
    "Jaromer": (50.3559, 15.9208),
    "Nove Mesto nad Metuji": (50.3440, 16.1506),
    "Cerveny Kostelec": (50.4754, 16.0930),
    "Dobruska": (50.2916, 16.1600),
    "Horice": (50.3664, 15.6316),
    "Kostelec nad Orlici": (50.1224, 16.2130),
    "Trebechovice pod Orebem": (50.2012, 15.9923),
    "Upice": (50.5131, 16.0157),
    "Nova Paka": (50.4960, 15.5154),

    # --- Additional cities in Pardubice Region ---
    "Vysoke Myto": (49.9546, 16.1592),
    "Litomysl": (49.8689, 16.3125),
    "Moravska Trebova": (49.7577, 16.6638),
    "Policka": (49.7144, 16.2655),
    "Zamberk": (50.0842, 16.4667),
    "Holice": (50.0597, 15.9856),
    "Hlinsko": (49.7623, 15.9074),
    "Letohrad": (50.0361, 16.4995),
    "Ceska Trebova": (49.9050, 16.4442),
    "Lanškroun": (49.9120, 16.6128),
    "Lanskroun": (49.9120, 16.6128),

    # --- Additional cities in Vysocina Region ---
    "Nove Mesto na Morave": (49.5613, 16.0744),
    "Svetla nad Sazavou": (49.6268, 15.4036),
    "Humpolec": (49.5418, 15.3594),
    "Pacov": (49.4706, 15.0013),
    "Chotebor": (49.7219, 15.6703),
    "Bystrice nad Pernstejnem": (49.5230, 16.2614),
    "Moravske Budejovice": (49.0518, 15.8070),
    "Velke Mezirici": (49.3555, 16.0123),
    "Telc": (49.1844, 15.4528),
    "Namest nad Oslavou": (49.2076, 16.1595),
    "Trebic-mesto": (49.2148, 15.8817),

    # --- Additional cities in South Moravian Region ---
    "Kurim": (49.2981, 16.5314),
    "Tisnov": (49.3486, 16.4241),
    "Boskovice": (49.4874, 16.6599),
    "Ivancice": (49.1018, 16.3775),
    "Slavkov u Brna": (49.1527, 16.8762),
    "Hrusovany nad Jevisovkou": (48.8266, 16.3939),
    "Mikulov": (48.8053, 16.6378),
    "Hustopece": (48.9403, 16.7370),
    "Kyjov": (49.0103, 17.1225),
    "Veseli nad Moravou": (48.9537, 17.3793),
    "Bucovice": (49.1499, 17.0005),
    "Rosice": (49.1812, 16.3879),
    "Letovice": (49.5474, 16.5740),
    "Moravsky Krumlov": (49.0487, 16.3125),
    "Pohorelice": (48.9831, 16.5213),

    # --- Additional cities in Olomouc Region ---
    "Zabreh": (49.8828, 16.8724),
    "Mohelnice": (49.7763, 16.9198),
    "Unicov": (49.7711, 17.1212),
    "Sternberk": (49.7305, 17.2992),
    "Litovel": (49.7012, 17.0760),
    "Kojetin": (49.3517, 17.3003),
    "Hranice": (49.5479, 17.7347),
    "Lipnik nad Becvou": (49.5271, 17.5862),

    # --- Additional cities in Zlin Region ---
    "Otrokovice": (49.2097, 17.5305),
    "Valasske Mezirici": (49.4718, 17.9718),
    "Roznov pod Radhostem": (49.4583, 18.1434),
    "Luhacovice": (49.1002, 17.7571),
    "Vizovice": (49.2215, 17.8530),
    "Slavicin": (49.0854, 17.8719),
    "Bystrice pod Hostynem": (49.3957, 17.6727),
    "Uhersky Brod": (49.0267, 17.6475),
    "Stare Mesto": (49.0756, 17.4318),
    "Bojkovice": (49.0379, 17.8196),
    "Napajedla": (49.1741, 17.5125),
    "Holešov": (49.3325, 17.5784),
    "Holesov": (49.3325, 17.5784),

    # --- Additional cities in Moravian-Silesian Region ---
    "Cesky Tesin": (49.7462, 18.6264),
    "Bohumin": (49.9040, 18.3567),
    "Orlova": (49.8455, 18.4302),
    "Hlucin": (49.8977, 18.1929),
    "Havirov-mesto": (49.7799, 18.4371),
    "Koprivnice": (49.5994, 18.1448),
    "Frenstat pod Radhostem": (49.5481, 18.2103),
    "Studénka": (49.7230, 18.0781),
    "Studenka": (49.7230, 18.0781),
    "Bilovec": (49.7556, 18.0150),
    "Vitkov": (49.7742, 17.7496),
    "Rymarov": (49.9318, 17.2713),
    "Krnov": (50.0895, 17.7036),
    "Petrvald": (49.8316, 18.3873),
    "Rychvald": (49.8662, 18.3757),
    "Senov": (49.7944, 18.3786),

    # --- Additional notable cities across regions ---
    "Uhersky Ostroh": (48.9872, 17.3879),
    "Frydlant nad Ostravici": (49.5923, 18.3579),
    "Bludov": (49.9448, 16.9283),
    "Zabreh na Morave": (49.8828, 16.8724),
    "Sternberk na Morave": (49.7305, 17.2992),
    "Pribor": (49.6412, 18.1450),
    "Odry": (49.6620, 17.8314),
    "Fulnek": (49.7125, 17.9026),
    "Bily Potok pod Smrkem": (50.9105, 15.2324),
    "Zelezna Ruda": (49.1381, 13.2349),
    "Spindleruv Mlyn": (50.7260, 15.6090),
    "Harrachov": (50.7731, 15.4289),
    "Karlstejn": (49.9394, 14.1881),
    "Cesky Sternberk": (49.8076, 14.9283),
    "Lednice": (48.7910, 16.8024),
    "Valtice": (48.7408, 16.7545),
    "Vranov nad Dyji": (48.8937, 15.8118),
    "Kromeriz-mesto": (49.2976, 17.3935),

    # --- Small towns found in GFR auction data ---
    "Hnojnik": (49.7135, 18.5308),
    "Hnojník": (49.7135, 18.5308),
    "Kozlany": (49.9946, 13.5264),
    "Kožlany": (49.9946, 13.5264),
    "Bechyne": (49.2962, 14.4676),
    "Bechyně": (49.2962, 14.4676),
    "Streckov": (50.6607, 14.0323),
    "Střekov": (50.6607, 14.0323),
    "Bohuslavice nad Metuji": (50.3126, 16.0894),
    "Bohuslavice nad Metují": (50.3126, 16.0894),
    "Brodek u Konice": (49.55, 16.8333),
    "Chudcice": (49.288, 16.458),
    "Chudčice": (49.288, 16.458),
    "Cvrcovice": (48.9937, 16.5145),
    "Cvrčovice": (48.9937, 16.5145),
    "Dobrockovice": (49.1630, 17.1048),
    "Dobročkovice": (49.1630, 17.1048),
    "Dolni Kamenice": (50.7979, 14.4067),
    "Dolní Kamenice": (50.7979, 14.4067),
    "Jamolice": (49.0731, 16.2533),
    "Kysice": (49.7533, 13.4862),
    "Kyšice": (49.7533, 13.4862),
    "Libotenice": (50.4769, 14.2289),
    "Lomnice nad Popelkou": (50.5306, 15.3734),
    "Malonin": (49.6333, 16.65),
    "Malonín": (49.6333, 16.65),
    "Milostin": (50.1941, 13.6679),
    "Milostín": (50.1941, 13.6679),
    "Milovice nad Labem": (50.2260, 14.8886),
    "Milovice": (50.2260, 14.8886),
    "Obrnice": (50.5050, 13.6954),
    "Osova Bityska": (49.3298, 16.1682),
    "Osová Bitýška": (49.3298, 16.1682),
    "Potstejn": (50.0822, 16.3092),
    "Potštejn": (50.0822, 16.3092),
    "Precaply": (50.4317, 13.4732),
    "Přečaply": (50.4317, 13.4732),
    "Prechovice": (49.18, 13.89),
    "Přechovice": (49.18, 13.89),
    "Rodinov": (49.2828, 15.1038),
    "Sobinov": (49.6982, 15.7594),
    "Sobíňov": (49.6982, 15.7594),
    "Stachy": (49.1018, 13.6666),
    "Valasska Senice": (49.2253, 18.117),
    "Valašská Senice": (49.2253, 18.117),
    "Vlastejovice": (49.7313, 15.1748),
    "Vlastějovice": (49.7313, 15.1748),
    "Zaluzi": (49.8427, 13.8605),
    "Záluží": (49.8427, 13.8605),
    "Zezice": (50.6861, 14.0705),
    "Žežice": (50.6861, 14.0705),
    "Minice": (50.2253, 14.2988),
    "Udlice": (50.4406, 13.4574),
    "Údlice": (50.4406, 13.4574),
    "Vlkov": (49.3215, 16.2051),
}


# =============================================================================
# DIACRITICS VARIANTS
#
# Maps Czech names with proper diacritics to the same coordinates.
# These are additional keys pointing to the same (lat, lng) tuples.
# =============================================================================

_DIACRITICS_VARIANTS: dict[str, tuple[float, float]] = {
    # Regional capitals
    "Plzeň": CZECH_CITIES["Plzen"],
    "České Budějovice": CZECH_CITIES["Ceske Budejovice"],
    "Hradec Králové": CZECH_CITIES["Hradec Kralove"],
    "Ústí nad Labem": CZECH_CITIES["Usti nad Labem"],
    "Zlín": CZECH_CITIES["Zlin"],
    "Karlovy Vary": CZECH_CITIES["Karlovy Vary"],  # No diacritics difference

    # District capitals - Central Bohemian
    "Benešov": CZECH_CITIES["Benesov"],
    "Kladno": CZECH_CITIES["Kladno"],  # No diacritics difference
    "Kolín": CZECH_CITIES["Kolin"],
    "Kutná Hora": CZECH_CITIES["Kutna Hora"],
    "Mělník": CZECH_CITIES["Melnik"],
    "Mladá Boleslav": CZECH_CITIES["Mlada Boleslav"],
    "Příbram": CZECH_CITIES["Pribram"],
    "Rakovník": CZECH_CITIES["Rakovnik"],
    "Praha-východ": CZECH_CITIES["Praha-vychod"],
    "Praha-západ": CZECH_CITIES["Praha-zapad"],

    # District capitals - South Bohemian
    "Český Krumlov": CZECH_CITIES["Cesky Krumlov"],
    "Jindřichův Hradec": CZECH_CITIES["Jindrichuv Hradec"],
    "Písek": CZECH_CITIES["Pisek"],
    "Tábor": CZECH_CITIES["Tabor"],

    # District capitals - Plzen Region
    "Domažlice": CZECH_CITIES["Domazlice"],
    "Plzeň-město": CZECH_CITIES["Plzen-mesto"],
    "Plzeň-jih": CZECH_CITIES["Plzen-jih"],
    "Plzeň-sever": CZECH_CITIES["Plzen-sever"],
    "Tachov": CZECH_CITIES["Tachov"],  # No diacritics difference

    # District capitals - Karlovy Vary Region
    "Cheb": CZECH_CITIES["Cheb"],  # No diacritics difference

    # District capitals - Usti nad Labem Region
    "Děčín": CZECH_CITIES["Decin"],
    "Litoměřice": CZECH_CITIES["Litomerice"],
    "Teplice": CZECH_CITIES["Teplice"],  # No diacritics difference
    "Ústí nad Labem-město": CZECH_CITIES["Usti nad Labem"],

    # District capitals - Liberec Region
    "Česká Lípa": CZECH_CITIES["Ceska Lipa"],
    "Jablonec nad Nisou": CZECH_CITIES["Jablonec nad Nisou"],  # No diacritics difference
    "Semily": CZECH_CITIES["Semily"],  # No diacritics difference

    # District capitals - Hradec Kralove Region
    "Jičín": CZECH_CITIES["Jicin"],
    "Náchod": CZECH_CITIES["Nachod"],
    "Rychnov nad Kněžnou": CZECH_CITIES["Rychnov nad Kneznou"],

    # District capitals - Pardubice Region
    "Chrudim": CZECH_CITIES["Chrudim"],  # No diacritics difference
    "Svitavy": CZECH_CITIES["Svitavy"],  # No diacritics difference
    "Ústí nad Orlicí": CZECH_CITIES["Usti nad Orlici"],

    # District capitals - Vysocina Region
    "Havlíčkův Brod": CZECH_CITIES["Havlickuv Brod"],
    "Pelhřimov": CZECH_CITIES["Pelhrimov"],
    "Třebíč": CZECH_CITIES["Trebic"],
    "Žďár nad Sázavou": CZECH_CITIES["Zdar nad Sazavou"],

    # District capitals - South Moravian Region
    "Břeclav": CZECH_CITIES["Breclav"],
    "Brno-město": CZECH_CITIES["Brno-mesto"],
    "Hodonín": CZECH_CITIES["Hodonin"],
    "Vyškov": CZECH_CITIES["Vyskov"],

    # District capitals - Olomouc Region
    "Jeseník": CZECH_CITIES["Jesenik"],
    "Prostějov": CZECH_CITIES["Prostejov"],
    "Přerov": CZECH_CITIES["Prerov"],
    "Šumperk": CZECH_CITIES["Sumperk"],

    # District capitals - Zlin Region
    "Kroměříž": CZECH_CITIES["Kromeriz"],
    "Uherské Hradiště": CZECH_CITIES["Uherske Hradiste"],
    "Vsetín": CZECH_CITIES["Vsetin"],

    # District capitals - Moravian-Silesian Region
    "Bruntál": CZECH_CITIES["Bruntal"],
    "Frýdek-Místek": CZECH_CITIES["Frydek-Mistek"],
    "Karviná": CZECH_CITIES["Karvina"],
    "Nový Jičín": CZECH_CITIES["Novy Jicin"],
    "Ostrava-město": CZECH_CITIES["Ostrava-mesto"],

    # Other notable cities - diacritics variants
    "Havířov": CZECH_CITIES["Havirov"],
    "Třinec": CZECH_CITIES["Trinec"],
    "Brandýs nad Labem-Stará Boleslav": CZECH_CITIES["Brandys nad Labem-Stara Boleslav"],
    "Brandýs nad Labem": CZECH_CITIES["Brandys nad Labem"],
    "Kralupy nad Vltavou": CZECH_CITIES["Kralupy nad Vltavou"],  # No difference
    "Říčany": CZECH_CITIES["Ricany"],
    "Sedlčany": CZECH_CITIES["Sedlcany"],
    "Slaný": CZECH_CITIES["Slany"],
    "Vlašim": CZECH_CITIES["Vlasim"],
    "Mnichovo Hradiště": CZECH_CITIES["Mnichovo Hradiste"],
    "Neratovice": CZECH_CITIES["Neratovice"],  # No difference
    "Lysá nad Labem": CZECH_CITIES["Lysa nad Labem"],
    "Poděbrady": CZECH_CITIES["Podebrady"],
    "Český Brod": CZECH_CITIES["Cesky Brod"],
    "Řevnice": CZECH_CITIES["Revnice"],
    "Černošice": CZECH_CITIES["Cernosice"],
    "Dobříš": CZECH_CITIES["Dobris"],
    "Nové Strašecí": CZECH_CITIES["Nove Straseci"],
    "Třeboň": CZECH_CITIES["Trebon"],
    "Milevsko": CZECH_CITIES["Milevsko"],  # No difference
    "Blatná": CZECH_CITIES["Blatna"],
    "Veselí nad Lužnicí": CZECH_CITIES["Veseli nad Luznici"],
    "Soběslav": CZECH_CITIES["Sobeslav"],
    "Dačice": CZECH_CITIES["Dacice"],
    "Týn nad Vltavou": CZECH_CITIES["Tyn nad Vltavou"],
    "Vodňany": CZECH_CITIES["Vodnany"],
    "Sušice": CZECH_CITIES["Susice"],
    "Horažďovice": CZECH_CITIES["Horazovice"],
    "Stříbro": CZECH_CITIES["Stribro"],
    "Horšovský Týn": CZECH_CITIES["Horsovsky Tyn"],
    "Královice": CZECH_CITIES["Kralovice"],
    "Nýrsko": CZECH_CITIES["Nyrsko"],
    "Mariánské Lázně": CZECH_CITIES["Marianske Lazne"],
    "Františkovy Lázně": CZECH_CITIES["Frantiskovy Lazne"],
    "Jáchymov": CZECH_CITIES["Jachymov"],
    "Nejdek": CZECH_CITIES["Nejdek"],  # No difference
    "Aš": CZECH_CITIES["As"],
    "Kraslice": CZECH_CITIES["Kraslice"],  # No difference
    "Bílina": CZECH_CITIES["Bilina"],
    "Kadaň": CZECH_CITIES["Kadan"],
    "Litvínov": CZECH_CITIES["Litvinov"],
    "Varnsdorf": CZECH_CITIES["Varnsdorf"],  # No difference
    "Roudnice nad Labem": CZECH_CITIES["Roudnice nad Labem"],  # No difference
    "Žatec": CZECH_CITIES["Zatec"],
    "Podbořany": CZECH_CITIES["Podborany"],
    "Turnov": CZECH_CITIES["Turnov"],  # No difference
    "Železný Brod": CZECH_CITIES["Zelezny Brod"],
    "Nový Bor": CZECH_CITIES["Novy Bor"],
    "Mimoň": CZECH_CITIES["Mimon"],
    "Frýdlant": CZECH_CITIES["Frydlant"],
    "Jilemnice": CZECH_CITIES["Jilemnice"],  # No difference
    "Dvůr Králové nad Labem": CZECH_CITIES["Dvur Kralove nad Labem"],
    "Nové Město nad Metují": CZECH_CITIES["Nove Mesto nad Metuji"],
    "Červený Kostelec": CZECH_CITIES["Cerveny Kostelec"],
    "Dobruška": CZECH_CITIES["Dobruska"],
    "Hořice": CZECH_CITIES["Horice"],
    "Kostelec nad Orlicí": CZECH_CITIES["Kostelec nad Orlici"],
    "Třebechovice pod Orebem": CZECH_CITIES["Trebechovice pod Orebem"],
    "Úpice": CZECH_CITIES["Upice"],
    "Nová Paka": CZECH_CITIES["Nova Paka"],
    "Vysoké Mýto": CZECH_CITIES["Vysoke Myto"],
    "Litomyšl": CZECH_CITIES["Litomysl"],
    "Moravská Třebová": CZECH_CITIES["Moravska Trebova"],
    "Polička": CZECH_CITIES["Policka"],
    "Žamberk": CZECH_CITIES["Zamberk"],
    "Hlinsko": CZECH_CITIES["Hlinsko"],  # No difference
    "Letohrad": CZECH_CITIES["Letohrad"],  # No difference
    "Česká Třebová": CZECH_CITIES["Ceska Trebova"],
    "Lanškroun": CZECH_CITIES["Lanskroun"],
    "Nové Město na Moravě": CZECH_CITIES["Nove Mesto na Morave"],
    "Světlá nad Sázavou": CZECH_CITIES["Svetla nad Sazavou"],
    "Chotěboř": CZECH_CITIES["Chotebor"],
    "Bystřice nad Pernštejnem": CZECH_CITIES["Bystrice nad Pernstejnem"],
    "Moravské Budějovice": CZECH_CITIES["Moravske Budejovice"],
    "Velké Meziříčí": CZECH_CITIES["Velke Mezirici"],
    "Telč": CZECH_CITIES["Telc"],
    "Náměšť nad Oslavou": CZECH_CITIES["Namest nad Oslavou"],
    "Třebíč-město": CZECH_CITIES["Trebic-mesto"],
    "Kuřim": CZECH_CITIES["Kurim"],
    "Tišnov": CZECH_CITIES["Tisnov"],
    "Ivančice": CZECH_CITIES["Ivancice"],
    "Slavkov u Brna": CZECH_CITIES["Slavkov u Brna"],  # No difference
    "Hrušovany nad Jevišovkou": CZECH_CITIES["Hrusovany nad Jevisovkou"],
    "Hustopeče": CZECH_CITIES["Hustopece"],
    "Kyjov": CZECH_CITIES["Kyjov"],  # No difference
    "Veselí nad Moravou": CZECH_CITIES["Veseli nad Moravou"],
    "Bučovice": CZECH_CITIES["Bucovice"],
    "Rosice": CZECH_CITIES["Rosice"],  # No difference
    "Moravský Krumlov": CZECH_CITIES["Moravsky Krumlov"],
    "Pohořelice": CZECH_CITIES["Pohorelice"],
    "Zábřeh": CZECH_CITIES["Zabreh"],
    "Uničov": CZECH_CITIES["Unicov"],
    "Šternberk": CZECH_CITIES["Sternberk"],
    "Kojetín": CZECH_CITIES["Kojetin"],
    "Lipník nad Bečvou": CZECH_CITIES["Lipnik nad Becvou"],
    "Otrokovice": CZECH_CITIES["Otrokovice"],  # No difference
    "Valašské Meziříčí": CZECH_CITIES["Valasske Mezirici"],
    "Rožnov pod Radhoštěm": CZECH_CITIES["Roznov pod Radhostem"],
    "Luhačovice": CZECH_CITIES["Luhacovice"],
    "Vizovice": CZECH_CITIES["Vizovice"],  # No difference
    "Slavičín": CZECH_CITIES["Slavicin"],
    "Bystřice pod Hostýnem": CZECH_CITIES["Bystrice pod Hostynem"],
    "Uherský Brod": CZECH_CITIES["Uhersky Brod"],
    "Staré Město": CZECH_CITIES["Stare Mesto"],
    "Napajedla": CZECH_CITIES["Napajedla"],  # No difference
    "Holešov": CZECH_CITIES["Holesov"],
    "Český Těšín": CZECH_CITIES["Cesky Tesin"],
    "Bohumín": CZECH_CITIES["Bohumin"],
    "Orlová": CZECH_CITIES["Orlova"],
    "Hlučín": CZECH_CITIES["Hlucin"],
    "Kopřivnice": CZECH_CITIES["Koprivnice"],
    "Frenštát pod Radhoštěm": CZECH_CITIES["Frenstat pod Radhostem"],
    "Studénka": CZECH_CITIES["Studenka"],
    "Bílovec": CZECH_CITIES["Bilovec"],
    "Vítkov": CZECH_CITIES["Vitkov"],
    "Rýmařov": CZECH_CITIES["Rymarov"],
    "Krnov": CZECH_CITIES["Krnov"],  # No difference
    "Frýdlant nad Ostravicí": CZECH_CITIES["Frydlant nad Ostravici"],
    "Příbor": CZECH_CITIES["Pribor"],
    "Špindlerův Mlýn": CZECH_CITIES["Spindleruv Mlyn"],
    "Karlštejn": CZECH_CITIES["Karlstejn"],
    "Český Šternberk": CZECH_CITIES["Cesky Sternberk"],
    "Lednice": CZECH_CITIES["Lednice"],  # No difference
    "Vranov nad Dyjí": CZECH_CITIES["Vranov nad Dyji"],
    "Kroměříž-město": CZECH_CITIES["Kromeriz-mesto"],
    "Železná Ruda": CZECH_CITIES["Zelezna Ruda"],
    "Vimperk": CZECH_CITIES["Vimperk"],  # No difference
    "Letovice": CZECH_CITIES["Letovice"],  # No difference
    "Bojkovice": CZECH_CITIES["Bojkovice"],  # No difference
    "Nepomuk": CZECH_CITIES["Nepomuk"],  # No difference
    "Plasy": CZECH_CITIES["Plasy"],  # No difference
    "Litovel": CZECH_CITIES["Litovel"],  # No difference
    "Hranice": CZECH_CITIES["Hranice"],  # No difference
    "Mohelnice": CZECH_CITIES["Mohelnice"],  # No difference
    "Petřvald": CZECH_CITIES["Petrvald"],
    "Šenov": CZECH_CITIES["Senov"],
    "Rychvald": CZECH_CITIES["Rychvald"],  # No difference
    "Odry": CZECH_CITIES["Odry"],  # No difference
    "Fulnek": CZECH_CITIES["Fulnek"],  # No difference
    "Rumburk": CZECH_CITIES["Rumburk"],  # No difference
    "Lovosice": CZECH_CITIES["Lovosice"],  # No difference
    "Jirkov": CZECH_CITIES["Jirkov"],  # No difference
    "Krupka": CZECH_CITIES["Krupka"],  # No difference
    "Doksy": CZECH_CITIES["Doksy"],  # No difference
    "Boskovice": CZECH_CITIES["Boskovice"],  # No difference
    "Mikulov": CZECH_CITIES["Mikulov"],  # No difference
    "Pacov": CZECH_CITIES["Pacov"],  # No difference
    "Hostivice": CZECH_CITIES["Hostivice"],  # No difference
    "Roztoky": CZECH_CITIES["Roztoky"],  # No difference
    "Humpolec": CZECH_CITIES["Humpolec"],  # No difference
    "Tanvald": CZECH_CITIES["Tanvald"],  # No difference
    "Duchcov": CZECH_CITIES["Duchcov"],  # No difference
    "Jaroměř": CZECH_CITIES["Jaromer"],
    "Holice": CZECH_CITIES["Holice"],  # No difference

    # Common alternate forms
    "Zlin": CZECH_CITIES["Zlin"],  # Already in main dict, but safe
    "Brno-venkov": CZECH_CITIES["Brno-venkov"],  # Already in main dict
    "Ždár nad Sázavou": CZECH_CITIES["Zdar nad Sazavou"],  # Alternate diacritics
}

# Merge diacritics variants into the main dictionary
CZECH_CITIES.update(_DIACRITICS_VARIANTS)


# =============================================================================
# GEOCODING FUNCTION
# =============================================================================

def _direct_lookup(city_name: str) -> Optional[tuple[float, float]]:
    """Direct exact + case-insensitive lookup only (no fuzzy matching)."""
    if city_name in CZECH_CITIES:
        return CZECH_CITIES[city_name]
    city_lower = city_name.lower()
    for key, coords in CZECH_CITIES.items():
        if key.lower() == city_lower:
            return coords
    return None


def geocode_czech_city(city_name: str) -> Optional[tuple[float, float]]:
    """
    Look up geographic coordinates for a Czech city name.

    Attempts matching in the following order:
    1. Exact / case-insensitive match against CZECH_CITIES keys
    2. "Praha X" district patterns → Praha coordinates
    3. "City - District" split on " - ", try the city part
    4. Comma-separated parts (address components), try each as city
    5. Substring match: check if any known city name appears in the input

    Args:
        city_name: The city name to look up. Can be with or without diacritics,
                   and may contain addresses, districts, or other suffixes.

    Returns:
        A (latitude, longitude) tuple if found, or None if the city is not
        in the database.

    Examples:
        >>> geocode_czech_city("Praha")
        (50.0755, 14.4378)
        >>> geocode_czech_city("Praha 4")
        (50.0755, 14.4378)
        >>> geocode_czech_city("Ústí nad Labem - Střekov")
        (50.6607, 14.0323)
        >>> geocode_czech_city("U Hlavního nádraží 4a, Jihlava")
        (49.3961, 15.5912)
    """
    if not city_name or not city_name.strip():
        return None

    name = city_name.strip()

    # 1. Exact / case-insensitive match
    result = _direct_lookup(name)
    if result:
        return result

    # 2. "Praha X" district pattern → Praha
    if re.match(r'^Praha\s*\d+', name, re.IGNORECASE):
        return CZECH_CITIES.get("Praha")

    # 3. "City - District" → try the city part
    if ' - ' in name:
        result = _direct_lookup(name.split(' - ')[0].strip())
        if result:
            return result

    # 4. Comma-separated parts (addresses like "Petrovická 221, Ústí nad Labem")
    if ',' in name:
        parts = [p.strip() for p in name.split(',')]
        for part in parts:
            result = _direct_lookup(part)
            if result:
                return result
            # Strip trailing house numbers: "Petrovická 221" → "Petrovická"
            cleaned = re.sub(r'\s+\d+[a-zA-Z]?(/\d+)?$', '', part).strip()
            if cleaned != part:
                result = _direct_lookup(cleaned)
                if result:
                    return result

    # 5. Substring match: check if any known city name appears in the input
    #    Use longest-match-first to avoid false positives (e.g. "Most" in other words)
    name_lower = name.lower()
    candidates = []
    for key, coords in CZECH_CITIES.items():
        if len(key) >= 4 and key.lower() in name_lower:
            candidates.append((len(key), key, coords))
    if candidates:
        candidates.sort(reverse=True)  # longest match first
        return candidates[0][2]

    return None


# =============================================================================
# MODULE SELF-TEST
# =============================================================================

if __name__ == "__main__":
    # Quick sanity check
    print(f"Total cities in database: {len(CZECH_CITIES)}")
    print()

    # Test regional capitals
    regional_capitals = [
        "Praha", "Brno", "Ostrava", "Plzen", "Liberec", "Olomouc",
        "Ceske Budejovice", "Hradec Kralove", "Usti nad Labem",
        "Pardubice", "Zlin", "Jihlava", "Karlovy Vary",
    ]
    print("Regional capitals:")
    for city in regional_capitals:
        coords = geocode_czech_city(city)
        print(f"  {city}: {coords}")

    print()

    # Test diacritics variants
    print("Diacritics variants:")
    for city in ["Plzeň", "České Budějovice", "Ústí nad Labem", "Kroměříž"]:
        coords = geocode_czech_city(city)
        print(f"  {city}: {coords}")

    print()

    # Test case-insensitive matching
    print("Case-insensitive matching:")
    for city in ["praha", "BRNO", "usti nad labem"]:
        coords = geocode_czech_city(city)
        print(f"  {city}: {coords}")

    print()

    # Test fuzzy matching (GFR auction patterns)
    print("Fuzzy matching (GFR patterns):")
    test_cases = [
        "Praha 1",
        "Praha 10",
        "Ústí nad Labem - Střekov",
        "U Hlavního nádraží 4a, Jihlava",
        "Petrovická 221, Ústí nad Labem",
        "Hnojník",
        "Kožlany",
        "Bechyně",
        "",
        "   ",
    ]
    for city in test_cases:
        coords = geocode_czech_city(city)
        print(f"  '{city}': {coords}")

    print()

    # Test unknown city
    print(f"Unknown city: {geocode_czech_city('Springfield')}")
