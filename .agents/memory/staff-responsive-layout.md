---
name: Staff workspace responsiveness
description: Responsive layouts must account for the persistent inbox sidebar and long conversation content.
---

Staff pages must remain usable in narrow and short windows, including when a desktop-sized window leaves a narrow main pane beside the inbox sidebar.

**Why:** The user repeatedly requested that all staff pages work at different window sizes. Viewport-only breakpoints missed the smaller content area beside the persistent inbox, especially in Quote AI chat.

**How to apply:** Base nested tool layouts on their available content width, not only the browser width. Check intermediate desktop widths as well as phones. Constrained conversations should scroll intact message boxes rather than shrink those boxes while their text paints outside them. Test long unbroken references, multiline replies, and access to the input and PDF action in short windows.
