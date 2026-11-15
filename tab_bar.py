"""Custom kitty tab bar: flat filled tabs with small gaps.

Loaded by `tab_bar_style custom` in kitty.conf. Tab colors come from the
active_tab_*/inactive_tab_* options there. The '+' new-tab button
(tab_bar_show_new_tab_button) is drawn through this function too.
"""

from kitty.tab_bar import as_rgb, draw_title
from kitty.utils import color_as_int

# cells around the title: 2 inner pads + 1 trailing gap
CHROME = 3


def draw_tab(draw_data, screen, tab, before, max_tab_length, index, is_last, extra_data):
    tab_bg = as_rgb(draw_data.tab_bg(tab))
    tab_fg = as_rgb(draw_data.tab_fg(tab))
    bar_bg = as_rgb(color_as_int(draw_data.default_bg))

    if max_tab_length < CHROME + 1:
        # no room for padding: plain label
        screen.cursor.bg = bar_bg
        screen.cursor.fg = tab_fg
        draw_title(draw_data, screen, tab, index, max(1, max_tab_length))
        return screen.cursor.x

    bold, italic = screen.cursor.bold, screen.cursor.italic
    screen.cursor.bold = screen.cursor.italic = False
    screen.cursor.bg = tab_bg
    screen.cursor.fg = tab_fg
    screen.draw(' ')

    budget = max_tab_length - CHROME
    title_x = screen.cursor.x
    screen.cursor.bold, screen.cursor.italic = bold, italic
    draw_title(draw_data, screen, tab, index, budget)
    drawn_end = screen.cursor.x
    if drawn_end > title_x + budget:
        screen.cursor.x = title_x + budget - 1
        screen.cursor.bg = tab_bg
        screen.cursor.fg = tab_fg
        screen.draw('…')

    screen.cursor.bold = screen.cursor.italic = False
    screen.cursor.bg = tab_bg
    screen.cursor.fg = tab_fg
    screen.draw(' ')
    screen.cursor.bg = bar_bg
    screen.cursor.fg = 0
    screen.draw(' ')
    end = screen.cursor.x
    if drawn_end > end:
        # blank whatever an over-long title drew past the tab
        screen.cursor.bg = bar_bg
        screen.draw(' ' * (drawn_end - end))
        screen.cursor.x = end
    screen.cursor.bold, screen.cursor.italic = bold, italic
    return end
