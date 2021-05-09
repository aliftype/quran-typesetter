#!/usr/bin/python
"""
Arabic numbers routins
@author: Taha Zerrouki
@contact: taha dot zerrouki at gmail dot com
@copyright: Arabtechies, Arabeyes, Taha Zerrouki
@license: GPL
@date:2017/02/14
@version: 0.3
# ArNumbers is imported from
license:   LGPL <http://www.gnu.org/licenses/lgpl.txt>
link      http://www.ar-php.org
category  Text
author    Khaled Al-Shamaa <khaled.alshamaa@gmail.com>
copyright 2009 Khaled Al-Shamaa
"""

INDIVIDUALS = {
0: ('', ''),
1: ('واحد', 'واحدة'),

2: (('إثنان', 'إثنين'), ('إثنتان', 'إثنتين')),

3: ('ثلاث', 'ثلاثة'),
4: ('أربع', 'أربعة'),
5: ('خمس', 'خمسة'),
6: ('ست', 'ستة'),
7: ('سبع', 'سبعة'),
8: ('ثماني', 'ثمانية'),
9: ('تسع', 'تسعة'),
10: ('عشر', 'عشرة'),

11: ('أحد عشر', 'إحدى عشرة'),

12: (('إثنا عشر',  'إثني عشر'), ('إثنتا عشرة', 'إثنتي عشرة')),

13: ('ثلاث عشرة', 'ثلاثة عشر'),

14: ('أربع عشرة', 'أربعة عشر'),
15: ('خمس عشرة', 'خمسة عشر'),
16: ('ست عشرة', 'ستة عشر'),
17: ('سبع عشرة', 'سبعة عشر'),
18: ('ثماني عشرة', 'ثمانية عشر'),
19: ('تسع عشرة', 'تسعة عشر'),

20: ('عشرون', 'عشرين', ),
30: ('ثلاثون', 'ثلاثين', ),
40: ('أربعون', 'أربعين', ),
50: ('خمسون', 'خمسين', ),
60: ('ستون', 'ستين', ),
70: ('سبعون', 'سبعين', ),
80: ('ثمانون', 'ثمانين', ),
90: ('تسعون', 'تسعين', ),

200: ('مائتان', 'مائتين'),

100: 'مائة',
300: 'ثلاثمائة',
400: 'أربعمائة',
500: 'خمسمائة',
600: 'ستمائة',
700: 'سبعمائة',
800: 'ثمانمائة',
900: 'تسعمائة',
}

def format_number(number, format_=0, feminine=0):
    if not isinstance(number, int):
        raise ValueError

    if number <= 0 or number >= 1000:
        raise ValueError
    
    items = []
    if number > 99:
        hundred = int(number / 100) * 100
        number = number % 100

        if hundred == 200:
            items.append(INDIVIDUALS[hundred][format_])
        else:
            items.append(INDIVIDUALS[hundred])
    if number == 2 or number == 12:
        items.append(INDIVIDUALS[number][feminine][format_])
    elif number < 20:
        items.append(INDIVIDUALS[number][feminine])
    else:
        ones = number % 10
        tens = int(number / 10) * 10

        items.append(INDIVIDUALS[tens][format_])
        if ones == 2:
            items.append( \
                INDIVIDUALS[ones][feminine][format_])
        elif ones > 0:
            items.append(INDIVIDUALS[ones][feminine])

    return ' و '.join(items)
