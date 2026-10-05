META = {"name": "watchlist", "description": "Report funding/extreme positioning from safe context",
        "trust": "trusted", "params": {}}
def run(context):
    lsr = context.get("long_short")
    fr = context.get("funding")
    if lsr is None:
        return "no crowd data in context"
    if lsr >= 2.0:
        return f"crowded LONG ({lsr}) — unwind risk; funding {fr}"
    if lsr <= 0.7:
        return f"crowded SHORT ({lsr}) — squeeze risk; funding {fr}"
    return f"balanced ({lsr}); funding {fr}"
