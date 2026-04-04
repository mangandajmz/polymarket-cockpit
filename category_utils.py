import re


CATEGORY_PATTERNS = {
    "Sports": [
        (r"\b(nba|nfl|nhl|mlb|soccer|football|basketball|baseball|hockey|tennis|golf|cricket|rugby)\b", 4),
        (r"\b(ufc|mma|boxing|olympics|super bowl|world cup|champions league|premier league|la liga|bundesliga|serie a|ligue 1)\b", 4),
        (r"\b(counter[- ]strike|cs2|valorant|dota ?2|league of legends|lol esports|esports?|overwatch|rocket league|call of duty)\b", 5),
        (r"\b(atp|wta|challenger|grand slam|roland garros|wimbledon|us open|australian open|french open)\b", 5),
        (r"\b(bo1|bo3|bo5|map 1|map 2|set 1|match point)\b", 3),
    ],
    "Politics": [
        (r"\b(election|president|congress|senate|house race|governor|mayor|parliament|prime minister|inauguration)\b", 5),
        (r"\b(biden|trump|harris|republican|democrat|gop|labour|conservative|liberal party)\b", 4),
        (r"\b(primary|ballot|vote|voter turnout|polls?)\b", 3),
    ],
    "Crypto": [
        (r"\b(bitcoin|btc|ethereum|eth|crypto|solana|defi|nft|dogecoin|doge|xrp|ripple|altcoin)\b", 5),
        (r"\b(coinbase|binance|polygon|matic|airdrop|memecoin|token)\b", 4),
    ],
    "Finance": [
        (r"\b(fed|fomc|interest rates?|gdp|inflation|cpi|recession|unemployment)\b", 5),
        (r"\b(sp500|s&p ?500|dow jones|nasdaq|stock market|earnings|ipo|treasury|yield)\b", 4),
    ],
    "Tech": [
        (r"\b(openai|chatgpt|tesla|spacex|apple|google|amazon|meta|microsoft|nvidia)\b", 4),
        (r"\b(ai|artificial intelligence|iphone|android|llm|model release)\b", 3),
    ],
}

MATCHUP_PATTERN = re.compile(r"\b.+\s(?:vs\.?|v\.?)\s.+\b", re.IGNORECASE)
SPORTS_EVENT_HINTS = re.compile(
    r"\b(open|cup|masters|classic|championship|playoffs|quarterfinal|semifinal|final)\b",
    re.IGNORECASE,
)


def classify_market_details(name: str) -> tuple[str, int, dict]:
    text = str(name or "").strip()
    if not text:
        return "Other", 0, {}

    normalized = re.sub(r"\s+", " ", text.lower())
    scores = {category: 0 for category in CATEGORY_PATTERNS}
    hits = {category: [] for category in CATEGORY_PATTERNS}

    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern, weight in patterns:
            if re.search(pattern, normalized, re.IGNORECASE):
                scores[category] += weight
                hits[category].append(pattern)

    if MATCHUP_PATTERN.search(normalized):
        if scores["Politics"] == 0 and scores["Finance"] == 0 and scores["Crypto"] == 0:
            scores["Sports"] += 3
            hits["Sports"].append("matchup")
        if SPORTS_EVENT_HINTS.search(normalized):
            scores["Sports"] += 2
            hits["Sports"].append("sports_event_hint")

    best_category = max(scores, key=scores.get)
    best_score = scores[best_category]
    if best_score <= 0:
        return "Other", 0, hits

    sorted_scores = sorted(scores.values(), reverse=True)
    if len(sorted_scores) > 1 and best_score == sorted_scores[1]:
        if best_category != "Sports" or "matchup" not in hits["Sports"]:
            return "Other", best_score, hits

    return best_category, best_score, hits


def classify_market(name: str) -> str:
    return classify_market_details(name)[0]
