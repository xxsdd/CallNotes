"""Bounded reading preferences shared by transcripts, reports and quotes."""

def reading_options(settings):
    def bounded(key, default, low, high):
        try:
            return max(low, min(high, int(settings.get(key, default))))
        except (TypeError, ValueError):
            return default
    return dict(size=bounded('reading_size', 17, 14, 24),
                spacing=bounded('reading_spacing', 180, 140, 220),
                width=bounded('reading_width', 820, 480, 1100))
