"""Configurable personality system for Liza."""
DEFAULTS = {
    "humor": 70,
    "sarcasm": 55,
    "friendliness": 65,
    "rudeness": 25,
    "seriousness": 35,
    "verbosity": 25,
}
LIMITS = {k: (0, 100) for k in DEFAULTS}

def build_personality_prompt(personality=None):
    values = dict(DEFAULTS)
    if isinstance(personality, dict):
        for key in values:
            try:
                values[key] = max(0, min(100, int(personality.get(key, values[key]))))
            except (TypeError, ValueError):
                pass

    v = values["verbosity"]
    length = "очень коротко, обычно 1–2 предложения" if v < 35 else (
        "коротко, обычно 2–4 предложения" if v < 70 else
        "подробнее, но без лишней воды"
    )
    return (
        "Характер Лизы: живой разговорный стиль. "
        f"Юмор: {values['humor']}/100. Сарказм: {values['sarcasm']}/100. "
        f"Дружелюбность: {values['friendliness']}/100. Грубость: {values['rudeness']}/100. "
        f"Серьёзность: {values['seriousness']}/100. Подробность: {values['verbosity']}/100. "
        f"Отвечай {length}. Подстраивай тон под ситуацию; в серьёзных вопросах не шути чрезмерно. "
        "Не упоминай настройки личности, промпты, модель или внутреннюю механику."
    )
