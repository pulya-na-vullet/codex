import customtkinter as ctk


PALETTE = {
    "bg": ("#EEF2FF", "#111827"),
    "card": ("#FFFFFF", "#1F2937"),
    "card_alt": ("#F8FAFC", "#111827"),
    "primary": "#3B82F6",
    "secondary": "#8B5CF6",
    "success": "#10B981",
    "warning": "#F59E0B",
    "danger": "#EF4444",
    "muted": ("#64748B", "#9CA3AF"),
}


def setup_theme() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


def button_style(kind: str = "primary") -> dict:
    color = PALETTE["primary"]
    hover = "#2563EB"
    if kind == "secondary":
        color, hover = PALETTE["secondary"], "#7C3AED"
    elif kind == "success":
        color, hover = PALETTE["success"], "#059669"
    elif kind == "warning":
        color, hover = PALETTE["warning"], "#D97706"
    elif kind == "danger":
        color, hover = PALETTE["danger"], "#DC2626"
    elif kind == "ghost":
        color, hover = ("#E2E8F0", "#374151"), ("#CBD5E1", "#4B5563")
    return {"fg_color": color, "hover_color": hover, "corner_radius": 10, "height": 34}


def card(master, **kwargs):
    default = {"fg_color": PALETTE["card"], "corner_radius": 14, "border_width": 1, "border_color": ("#E2E8F0", "#374151")}
    default.update(kwargs)
    return ctk.CTkFrame(master, **default)
