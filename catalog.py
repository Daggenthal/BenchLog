"""Seed catalog: device types, their test checklists, and common failure parts.

Each part is a tuple:
    (name, part_number, purpose, default_cost, needs_microsoldering, [functions it fixes], is_upgrade)

Default costs are rough placeholders in USD. Edit them in the Catalog page to
match what you actually pay. Part numbers are only filled in where they are
well known, so an empty part number does not mean the part has none.
"""


def P(name, number, purpose, cost, micro, fixes, upgrade=False):
    return (name, number, purpose, cost, micro, fixes, upgrade)


# GuliKit drift-proof sticks. They read position with magnets instead of a wiper on a
# carbon track, so there is nothing to wear out. Prices are per stick, taken from
# pair prices seen in late 2026: check current listings before quoting a customer.
TMR_NOTE = ("Contactless TMR sensor that does not wear out. Needs soldering, then calibration with "
            "GuliKit's tool. Sold in pairs. Check the listing covers this controller")


def gulikit_tmr(side, stick_function, click_function):
    return P("GuliKit TMR stick, %s (drift-proof upgrade)" % side, "", TMR_NOTE, 15.0, 1,
             [stick_function, click_function], upgrade=True)


# ---------------------------------------------------------------- controllers

DUALSENSE = {
    "name": "DualSense (PS5 controller)",
    "category": "Controller",
    "functions": [
        "Powers on", "Charges over USB-C", "Works wired over USB", "Pairs over Bluetooth",
        "Battery holds charge", "Left stick (no drift)", "Right stick (no drift)",
        "L3 click", "R3 click", "D-pad", "Face buttons", "L1", "R1",
        "L2 trigger", "R2 trigger", "Adaptive trigger resistance",
        "Touchpad touch", "Touchpad click", "Create / Options / PS buttons",
        "Mute button and LED", "Haptics", "Speaker", "Microphone",
        "Headphone jack", "Light bar", "Shell and cosmetics",
    ],
    "parts": [
        P("Analog stick module (left)", "", "Fixes drift and a dead or sticky L3 click", 3.0, 1,
          ["Left stick (no drift)", "L3 click"]),
        P("Analog stick module (right)", "", "Fixes drift and a dead or sticky R3 click", 3.0, 1,
          ["Right stick (no drift)", "R3 click"]),
        P("Stick potentiometers (pair)", "", "Cheaper drift fix that keeps the original stick body", 1.0, 1,
          ["Left stick (no drift)", "Right stick (no drift)"]),
        gulikit_tmr("left", "Left stick (no drift)", "L3 click"),
        gulikit_tmr("right", "Right stick (no drift)", "R3 click"),
        P("Thumbstick caps", "", "Worn or torn rubber caps", 1.0, 0, ["Shell and cosmetics"]),
        P("USB-C charging port", "", "No charge, loose cable, no wired connection", 2.0, 1,
          ["Charges over USB-C", "Works wired over USB"]),
        P("Battery", "LIP1708", "Short battery life or will not hold charge", 10.0, 0,
          ["Battery holds charge", "Powers on"]),
        P("Conductive button film", "", "Dead or intermittent face buttons and D-pad", 3.0, 0,
          ["D-pad", "Face buttons", "Create / Options / PS buttons"]),
        P("Rubber button pads", "", "Mushy or stuck buttons", 2.0, 0, ["D-pad", "Face buttons"]),
        P("L1 / R1 buttons and flex", "", "Dead bumpers", 3.0, 0, ["L1", "R1"]),
        P("L2 adaptive trigger assembly", "", "Dead, loose, or grinding left trigger", 6.0, 0,
          ["L2 trigger", "Adaptive trigger resistance"]),
        P("R2 adaptive trigger assembly", "", "Dead, loose, or grinding right trigger", 6.0, 0,
          ["R2 trigger", "Adaptive trigger resistance"]),
        P("Trigger springs", "", "Trigger does not return", 1.0, 0, ["L2 trigger", "R2 trigger"]),
        P("Touchpad assembly", "", "No touch input or dead touchpad click", 8.0, 0,
          ["Touchpad touch", "Touchpad click"]),
        P("Touchpad flex cable", "", "Intermittent touchpad", 2.0, 0, ["Touchpad touch"]),
        P("Haptic actuator (left)", "", "No or rattling haptics on the left", 5.0, 1, ["Haptics"]),
        P("Haptic actuator (right)", "", "No or rattling haptics on the right", 5.0, 1, ["Haptics"]),
        P("Speaker", "", "No controller audio", 3.0, 1, ["Speaker"]),
        P("Microphone board", "", "Mic not picked up, mute button dead", 4.0, 0,
          ["Microphone", "Mute button and LED"]),
        P("Headphone jack", "", "No audio from headset jack", 2.0, 1, ["Headphone jack"]),
        P("Light bar flex / LED board", "", "Light bar dark", 3.0, 0, ["Light bar"]),
        P("Main board", "", "Donor board swap when the board is beyond repair", 20.0, 0,
          ["Powers on", "Pairs over Bluetooth"]),
        P("Shell (front, back, or trim)", "", "Cracked or worn housing", 8.0, 0, ["Shell and cosmetics"]),
    ],
}

DUALSHOCK4 = {
    "name": "DualShock 4 (PS4 controller)",
    "category": "Controller",
    "functions": [
        "Powers on", "Charges over micro-USB", "Works wired over USB", "Pairs over Bluetooth",
        "Battery holds charge", "Left stick (no drift)", "Right stick (no drift)",
        "L3 click", "R3 click", "D-pad", "Face buttons", "L1", "R1",
        "L2 trigger", "R2 trigger", "Touchpad touch", "Touchpad click",
        "Share / Options / PS buttons", "Rumble", "Speaker", "Headphone jack",
        "Light bar", "Shell and cosmetics",
    ],
    "parts": [
        P("Analog stick module (left)", "", "Fixes drift and a dead L3 click", 2.5, 1,
          ["Left stick (no drift)", "L3 click"]),
        P("Analog stick module (right)", "", "Fixes drift and a dead R3 click", 2.5, 1,
          ["Right stick (no drift)", "R3 click"]),
        gulikit_tmr("left", "Left stick (no drift)", "L3 click"),
        gulikit_tmr("right", "Right stick (no drift)", "R3 click"),
        P("Thumbstick caps", "", "Worn or torn rubber caps", 1.0, 0, ["Shell and cosmetics"]),
        P("Micro-USB charging port board", "", "No charge or loose cable. Match the JDS/JDM board revision", 3.0, 0,
          ["Charges over micro-USB", "Works wired over USB"]),
        P("Charging port flex cable", "", "Port board is fine but still no charge", 2.0, 0,
          ["Charges over micro-USB"]),
        P("Battery", "LIP1522", "Short battery life or will not hold charge", 8.0, 0,
          ["Battery holds charge", "Powers on"]),
        P("Conductive button film", "", "The classic fix for dead or ghosting buttons. Match the board revision", 3.0, 0,
          ["D-pad", "Face buttons", "L1", "R1", "L2 trigger", "R2 trigger", "Share / Options / PS buttons"]),
        P("Rubber button pads", "", "Mushy or stuck buttons", 2.0, 0, ["D-pad", "Face buttons"]),
        P("L2 / R2 triggers and springs", "", "Trigger broken or does not return", 2.0, 0,
          ["L2 trigger", "R2 trigger"]),
        P("Touchpad assembly", "", "No touch input or dead touchpad click", 6.0, 0,
          ["Touchpad touch", "Touchpad click"]),
        P("Touchpad flex cable", "", "Intermittent touchpad", 2.0, 0, ["Touchpad touch"]),
        P("Rumble motor (left)", "", "No rumble or rattle on the left", 3.0, 1, ["Rumble"]),
        P("Rumble motor (right)", "", "No rumble or rattle on the right", 3.0, 1, ["Rumble"]),
        P("Speaker", "", "No controller audio", 2.0, 0, ["Speaker"]),
        P("Headphone jack and EXT port board", "", "No headset audio", 3.0, 0, ["Headphone jack"]),
        P("Light bar flex", "", "Light bar dark", 2.0, 0, ["Light bar"]),
        P("Main board", "", "Donor board swap when the board is beyond repair", 15.0, 0,
          ["Powers on", "Pairs over Bluetooth"]),
        P("Shell (front or back)", "", "Cracked or worn housing", 7.0, 0, ["Shell and cosmetics"]),
    ],
}


def xbox_controller(name, port_name, port_function):
    return {
        "name": name,
        "category": "Controller",
        "functions": [
            "Powers on", port_function, "Pairs wirelessly", "Battery contacts / door",
            "Left stick (no drift)", "Right stick (no drift)", "LS click", "RS click",
            "D-pad", "Face buttons", "LB", "RB", "LT trigger", "RT trigger",
            "View / Menu / Xbox buttons", "Sync button", "Rumble (grips)",
            "Impulse trigger rumble", "Headphone jack", "Shell and cosmetics",
        ],
        "parts": [
            P("Analog stick module (left)", "", "Fixes drift and a dead LS click", 2.5, 1,
              ["Left stick (no drift)", "LS click"]),
            P("Analog stick module (right)", "", "Fixes drift and a dead RS click", 2.5, 1,
              ["Right stick (no drift)", "RS click"]),
            gulikit_tmr("left", "Left stick (no drift)", "LS click"),
            gulikit_tmr("right", "Right stick (no drift)", "RS click"),
            P("Thumbstick caps", "", "Worn or torn rubber caps", 1.0, 0, ["Shell and cosmetics"]),
            P("LB / RB bumper assembly", "", "Snapped plastic bumper bar, the most common Xbox fault", 3.0, 0,
              ["LB", "RB"]),
            P("LB / RB tactile switches", "", "Bumper bar is fine but no click registers", 1.0, 1, ["LB", "RB"]),
            P(port_name, "", "No wired connection or no charge with a play-and-charge pack", 2.0, 1,
              [port_function]),
            P("Trigger (LT / RT) and spring", "", "Broken or sticking trigger", 2.0, 0,
              ["LT trigger", "RT trigger"]),
            P("Trigger hall sensor / magnet", "", "Trigger moves but reads wrong", 2.0, 1,
              ["LT trigger", "RT trigger"]),
            P("D-pad and conductive pad", "", "Dead or mushy D-pad", 2.0, 0, ["D-pad"]),
            P("Face button conductive pad", "", "Dead or mushy A/B/X/Y", 2.0, 0, ["Face buttons"]),
            P("Rumble motor (grip, left)", "", "No rumble or rattle on the left", 3.0, 1, ["Rumble (grips)"]),
            P("Rumble motor (grip, right)", "", "No rumble or rattle on the right", 3.0, 1, ["Rumble (grips)"]),
            P("Impulse trigger motor", "", "No rumble in a trigger", 3.0, 1, ["Impulse trigger rumble"]),
            P("Headphone jack", "", "No headset audio", 2.0, 1, ["Headphone jack"]),
            P("Sync button switch", "", "Cannot enter pairing mode", 1.0, 1, ["Sync button", "Pairs wirelessly"]),
            P("Battery contact springs", "", "Cuts out when moved, corrosion from leaked cells", 2.0, 1,
              ["Battery contacts / door", "Powers on"]),
            P("Battery door", "", "Missing or broken door", 2.0, 0, ["Battery contacts / door"]),
            P("Main board set (top and bottom)", "", "Donor board swap when the board is beyond repair", 15.0, 0,
              ["Powers on", "Pairs wirelessly"]),
            P("Shell (faceplate or back)", "", "Cracked or worn housing", 7.0, 0, ["Shell and cosmetics"]),
        ],
    }


def joycon(name, side, extra_functions, extra_parts, generation=1):
    functions = [
        "Powers on", "Charges on the console", "Pairs wirelessly", "Detected when attached",
        "Battery holds charge", "Stick (no drift)", "Stick click",
        "Direction / face buttons", "Shoulder button (%s)" % ("L" if side == "L" else "R"),
        "Trigger (%s)" % ("ZL" if side == "L" else "ZR"), "SL / SR buttons", "Sync button",
        "Rumble", "Player LEDs", "Locks onto the console", "Shell and cosmetics",
    ] + extra_functions
    parts = [
        P("Analog stick module", "", "Fixes drift. The same module fits left and right", 3.0, 0,
          ["Stick (no drift)", "Stick click"]),
        P("Battery", "HAC-006" if generation == 1 else "", "Short battery life or will not charge", 8.0, 0,
          ["Battery holds charge", "Powers on"]),
        P("Shoulder and trigger flex cable", "", "Dead L/R or ZL/ZR", 3.0, 0,
          [functions[8], functions[9]]),
        P("SL / SR and LED flex cable", "", "Dead SL/SR, sync button, or player LEDs", 3.0, 0,
          ["SL / SR buttons", "Sync button", "Player LEDs"]),
        P("Rumble motor", "", "No rumble or buzzing rattle", 4.0, 0, ["Rumble"]),
        P("Conductive button pad", "", "Mushy or dead buttons", 2.0, 0, ["Direction / face buttons"]),
        P("Main board", "", "Donor board swap when the board is beyond repair", 12.0, 0,
          ["Powers on", "Pairs wirelessly"]),
        P("Shell and midframe", "", "Cracked or worn housing", 6.0, 0, ["Shell and cosmetics"]),
    ]
    if generation == 1:
        parts += [
            P("GuliKit TMR stick (drift-proof upgrade)", "NS40T",
              "Contactless sensor that does not wear out. Drop-in, no soldering. Recalibrate the sticks in "
              "system settings afterwards. Sold in pairs, and the same stick fits left and right", 12.5, 0,
              ["Stick (no drift)", "Stick click"], upgrade=True),
            P("Slider rail with flex cable", "", "Not detected or not charging when attached", 4.0, 0,
              ["Charges on the console", "Detected when attached"]),
            P("Lock latch (metal buckle)", "", "Joy-Con slides off the console", 1.0, 0,
              ["Locks onto the console"]),
        ]
    else:
        parts += [
            P("Magnetic connector assembly", "", "Not detected, not charging, or loose when attached", 6.0, 0,
              ["Charges on the console", "Detected when attached", "Locks onto the console"]),
            P("Release button mechanism", "", "Will not detach or will not hold", 3.0, 0,
              ["Locks onto the console"]),
            P("Mouse sensor", "", "Mouse mode does not track", 5.0, 0, ["Mouse mode"]),
        ]
    return {"name": name, "category": "Controller", "functions": functions, "parts": parts + extra_parts}


JOYCON_L = joycon("Joy-Con (L)", "L",
                  ["Capture button", "Minus button"],
                  [P("Capture / minus button switches", "", "Dead capture or minus button", 1.0, 1,
                     ["Capture button", "Minus button"])])
JOYCON_R = joycon("Joy-Con (R)", "R",
                  ["Home button", "Plus button", "NFC (amiibo)", "IR camera"],
                  [P("Home / plus button switches", "", "Dead home or plus button", 1.0, 1,
                     ["Home button", "Plus button"]),
                   P("NFC antenna", "", "Amiibo not read", 3.0, 0, ["NFC (amiibo)"]),
                   P("IR camera module", "", "IR camera not working", 5.0, 0, ["IR camera"])])
JOYCON2_L = joycon("Joy-Con 2 (L)", "L",
                   ["Capture button", "Minus button", "Mouse mode"],
                   [P("Capture / minus button switches", "", "Dead capture or minus button", 1.0, 1,
                      ["Capture button", "Minus button"])], generation=2)
JOYCON2_R = joycon("Joy-Con 2 (R)", "R",
                   ["Home button", "Plus button", "C button", "NFC (amiibo)", "Mouse mode"],
                   [P("Home / plus / C button switches", "", "Dead home, plus, or C button", 1.0, 1,
                      ["Home button", "Plus button", "C button"]),
                    P("NFC antenna", "", "Amiibo not read", 3.0, 0, ["NFC (amiibo)"])], generation=2)

SWITCH_PRO = {
    "name": "Switch Pro Controller",
    "category": "Controller",
    "functions": [
        "Powers on", "Charges over USB-C", "Works wired over USB", "Pairs wirelessly",
        "Battery holds charge", "Left stick (no drift)", "Right stick (no drift)",
        "L3 click", "R3 click", "D-pad", "Face buttons", "L", "R", "ZL trigger", "ZR trigger",
        "Plus / Minus / Home / Capture", "Sync button", "HD rumble", "NFC (amiibo)",
        "Player LEDs", "Shell and cosmetics",
    ],
    "parts": [
        P("Analog stick module (left)", "", "Fixes drift and a dead L3 click", 3.0, 1,
          ["Left stick (no drift)", "L3 click"]),
        P("Analog stick module (right)", "", "Fixes drift and a dead R3 click", 3.0, 1,
          ["Right stick (no drift)", "R3 click"]),
        gulikit_tmr("left", "Left stick (no drift)", "L3 click"),
        gulikit_tmr("right", "Right stick (no drift)", "R3 click"),
        P("Thumbstick caps", "", "Worn caps and the white dust they leave in the stick", 1.0, 0,
          ["Shell and cosmetics"]),
        P("USB-C charging port", "", "No charge or no wired connection", 2.0, 1,
          ["Charges over USB-C", "Works wired over USB"]),
        P("Battery", "CTR-003", "Short battery life or will not hold charge", 9.0, 0,
          ["Battery holds charge", "Powers on"]),
        P("D-pad and conductive pad", "", "Wrong or doubled D-pad inputs", 2.0, 0, ["D-pad"]),
        P("Face button conductive pad", "", "Dead or mushy A/B/X/Y", 2.0, 0, ["Face buttons"]),
        P("L / R button switches", "", "Dead shoulder buttons", 1.0, 1, ["L", "R"]),
        P("ZL / ZR trigger and switch", "", "Dead triggers", 2.0, 0, ["ZL trigger", "ZR trigger"]),
        P("HD rumble motor (left)", "", "No rumble on the left", 4.0, 1, ["HD rumble"]),
        P("HD rumble motor (right)", "", "No rumble on the right", 4.0, 1, ["HD rumble"]),
        P("NFC antenna", "", "Amiibo not read", 3.0, 0, ["NFC (amiibo)"]),
        P("Main board", "", "Donor board swap when the board is beyond repair", 18.0, 0,
          ["Powers on", "Pairs wirelessly"]),
        P("Shell and grips", "", "Cracked or worn housing", 8.0, 0, ["Shell and cosmetics"]),
    ],
}

# ------------------------------------------------------------------- consoles


def switch_console(name, kind):
    """kind is one of: v1, lite, oled, switch2"""
    handheld_only = kind == "lite"
    functions = ["Powers on", "Boots to the home screen", "Charges over USB-C", "Battery holds charge"]
    if not handheld_only:
        functions += ["Docks and outputs to TV"]
    functions += ["Display image", "Display backlight" if kind != "oled" else "Display brightness",
                  "Touchscreen", "Game card slot", "microSD slot" if kind != "switch2" else "microSD Express slot",
                  "Wi-Fi", "Bluetooth and wireless controllers", "Speakers", "Headphone jack",
                  "Power button", "Volume buttons", "Fan and cooling"]
    if handheld_only:
        functions += ["Left stick (no drift)", "Right stick (no drift)", "D-pad and face buttons",
                      "L / R / ZL / ZR", "Rumble"]
    else:
        functions += ["Left Joy-Con detected and charges", "Right Joy-Con detected and charges", "Kickstand"]
    if kind == "switch2":
        functions += ["Top USB-C port", "Microphone"]
    functions += ["Not banned / no error codes", "Shell and cosmetics"]

    dock = [] if handheld_only else ["Docks and outputs to TV"]
    known = kind != "switch2"  # chip part numbers are only filled in for the original family

    parts = [
        P("USB-C charging port", "", "No charge, loose cable, no dock output. Check for bent pins first", 4.0, 1,
          ["Charges over USB-C"] + dock),
        P("USB-C power delivery IC", "M92T36" if known else "",
          "Negotiates charging and dock power. Often killed by a damaged port or a bad third-party dock", 6.0, 1,
          ["Charges over USB-C", "Powers on"] + dock),
        P("Battery charger IC", "BQ24193" if known else "",
          "Charges the battery and feeds system power. Suspect it when charging is very slow or stops", 5.0, 1,
          ["Charges over USB-C", "Powers on", "Battery holds charge"]),
        P("Main power management IC", "MAX77620" if known else "",
          "Generates the main voltage rails. No rails means no boot", 8.0, 1,
          ["Powers on", "Boots to the home screen"]),
        P("Battery fuel gauge IC", "MAX17050" if known else "",
          "Reports battery level. A fault here can stop boot or show wrong percentages", 4.0, 1,
          ["Battery holds charge", "Powers on"]),
        P("USB-C line filters and fuses", "", "Small parts between the port and the chips that burn open", 1.0, 1,
          ["Charges over USB-C"] + dock),
        P("Battery", {"v1": "HAC-003", "lite": "HDH-003"}.get(kind, ""),
          "Short battery life, swollen pack, or will not hold charge", 15.0, 0,
          ["Battery holds charge", "Powers on"]),
        P("Game card reader" + (" board (with headphone jack)" if kind in ("v1", "oled") else ""), "",
          "Game cards not read", 12.0, 0 if kind in ("v1", "oled") else 1,
          ["Game card slot"] + (["Headphone jack"] if kind in ("v1", "oled") else [])),
        P("Game card slot pins", "", "Bent pins from a jammed or counterfeit card", 5.0, 1, ["Game card slot"]),
        P("microSD reader" + (" board" if kind == "v1" else ""), "", "microSD cards not detected", 6.0,
          0 if kind == "v1" else 1, [functions[functions.index("Game card slot") + 1]]),
        P("Fan", "", "Loud, rattling, or not spinning", 8.0, 0, ["Fan and cooling"]),
        P("Thermal paste", "", "Overheating and shutdowns", 1.0, 0, ["Fan and cooling"]),
        P("Speakers", "", "No or crackling audio", 5.0, 0, ["Speakers"]),
        P("Wi-Fi / Bluetooth IC", "BCM4356" if known else "",
          "No Wi-Fi and no wireless controllers at the same time", 8.0, 1,
          ["Wi-Fi", "Bluetooth and wireless controllers"]),
        P("Power and volume button flex", "", "Dead power or volume buttons", 4.0, 0,
          ["Power button", "Volume buttons"]),
        P("Display connector", "", "Lines or no image after a drop, connector lifted from the board", 2.0, 1,
          ["Display image"]),
        P("Main board", "", "Donor board swap. A board from a banned console stays banned", 60.0, 0,
          ["Powers on", "Boots to the home screen", "Not banned / no error codes"]),
        P("Shell (back plate, midframe)", "", "Cracked or worn housing", 10.0, 0, ["Shell and cosmetics"]),
    ]
    if not handheld_only:
        parts += [
            P("USB / video switch IC", "PI3USB30532" if known else "",
              "Routes video to the dock. Charges fine in the dock but no TV picture", 5.0, 1,
              ["Docks and outputs to TV"]),
            P("Left Joy-Con rail (with flex)" if kind != "switch2" else "Left Joy-Con magnetic connector", "",
              "Left Joy-Con not detected or not charging", 6.0, 0, ["Left Joy-Con detected and charges"]),
            P("Right Joy-Con rail (with flex)" if kind != "switch2" else "Right Joy-Con magnetic connector", "",
              "Right Joy-Con not detected or not charging", 6.0, 0, ["Right Joy-Con detected and charges"]),
            P("Kickstand", "", "Missing or snapped kickstand", 3.0, 0, ["Kickstand"]),
        ]
    if kind == "oled":
        parts += [
            P("OLED panel (with touch)", "", "Cracked, dead, or burnt-in screen", 60.0, 0,
              ["Display image", "Display brightness", "Touchscreen"]),
        ]
    else:
        parts += [
            P("LCD panel", "", "Cracked, dead, or lined screen", 30.0, 0, ["Display image"]),
            P("Touch digitizer", "", "Cracked glass or no touch", 12.0, 0, ["Touchscreen"]),
            P("Backlight circuit (driver, diode, coil)", "", "Image is there but very dark", 3.0, 1,
              ["Display backlight"]),
        ]
    if kind == "v1":
        parts += [P("eMMC storage module", "", "Removable storage board. Console data is tied to its own board",
                    25.0, 0, ["Boots to the home screen"])]
    else:
        parts += [P("eMMC storage (soldered)", "", "Boot loops or storage errors", 25.0, 1,
                    ["Boots to the home screen"])]
    if kind == "lite":
        parts += [
            P("Analog stick module (left)", "", "Fixes drift on the left stick", 4.0, 0, ["Left stick (no drift)"]),
            P("Analog stick module (right)", "", "Fixes drift on the right stick", 4.0, 0,
              ["Right stick (no drift)"]),
            P("GuliKit TMR stick (drift-proof upgrade)", "NS40T",
              "Contactless sensor that does not wear out. The Joy-Con stick also fits the Lite. Recalibrate "
              "the sticks in system settings afterwards. Sold in pairs", 12.5, 0,
              ["Left stick (no drift)", "Right stick (no drift)"], upgrade=True),
            P("Button conductive pads", "", "Mushy or dead buttons", 3.0, 0, ["D-pad and face buttons"]),
            P("L / R / ZL / ZR flex and switches", "", "Dead shoulder buttons or triggers", 4.0, 0,
              ["L / R / ZL / ZR"]),
            P("Rumble motor", "", "No rumble or rattle", 4.0, 0, ["Rumble"]),
            P("Headphone jack board", "", "No headset audio", 5.0, 0, ["Headphone jack"]),
        ]
    if kind == "switch2":
        parts += [
            P("Top USB-C port", "", "Top port dead or loose", 4.0, 1, ["Top USB-C port"]),
            P("Microphone", "", "Built-in mic not picked up", 3.0, 1, ["Microphone"]),
            P("Headphone jack", "", "No headset audio", 4.0, 1, ["Headphone jack"]),
        ]
    return {"name": name, "category": "Console", "functions": functions, "parts": parts}


def steam_deck(name, oled):
    return {
        "name": name,
        "category": "Handheld PC",
        "functions": [
            "Powers on", "Boots to SteamOS", "Charges over USB-C", "USB-C data and video out",
            "Battery holds charge", "Display image", "Touchscreen",
            "Left stick (no drift)", "Right stick (no drift)", "L3 / R3 click", "Stick touch sensing",
            "D-pad", "Face buttons", "L1", "R1", "L2 trigger", "R2 trigger",
            "Back grip buttons (L4 L5 R4 R5)", "Left trackpad", "Right trackpad",
            "Steam / Quick Access / View / Menu buttons", "Power button", "Volume buttons",
            "Speakers", "Headphone jack", "Microphone", "Wi-Fi", "Bluetooth", "microSD slot",
            "Fan and cooling", "Haptics", "Shell and cosmetics",
        ],
        "parts": [
            P("Thumbstick module (left)", "", "Fixes drift. Match the stick type printed on the old module", 20.0, 0,
              ["Left stick (no drift)", "L3 / R3 click", "Stick touch sensing"]),
            P("Thumbstick module (right)", "", "Fixes drift. Match the stick type printed on the old module", 20.0, 0,
              ["Right stick (no drift)", "L3 / R3 click", "Stick touch sensing"]),
            P("GuliKit Hall stick, left (drift-proof upgrade)", "" if oled else "SD02",
              ("Contactless Hall sensor. OLED version only: the LCD kit does not fit. " if oled else
               "Contactless Hall sensor. Fits Type A and Type B LCD models, not the OLED. ")
              + "Drop-in, no soldering. Press the calibration button on the module before closing up. Sold in pairs",
              12.5 if oled else 9.5, 0, ["Left stick (no drift)", "L3 / R3 click"], upgrade=True),
            P("GuliKit Hall stick, right (drift-proof upgrade)", "" if oled else "SD02",
              ("Contactless Hall sensor. OLED version only: the LCD kit does not fit. " if oled else
               "Contactless Hall sensor. Fits Type A and Type B LCD models, not the OLED. ")
              + "Drop-in, no soldering. Press the calibration button on the module before closing up. Sold in pairs",
              12.5 if oled else 9.5, 0, ["Right stick (no drift)", "L3 / R3 click"], upgrade=True),
            P("USB-C charging port", "", "No charge or loose cable. This is the real soldering job on a Deck", 4.0, 1,
              ["Charges over USB-C", "USB-C data and video out"]),
            P("Charging circuit (charge IC, fuses)", "", "Port is good but still no charge or no power", 8.0, 1,
              ["Charges over USB-C", "Powers on"]),
            P("Battery", "", "Short battery life or swollen pack. Glued in, so use heat and care", 60.0, 0,
              ["Battery holds charge", "Powers on"]),
            P("Left daughterboard", "", "D-pad, left trackpad, or left stick input dead. Revisions are not interchangeable", 30.0, 0,
              ["D-pad", "Left trackpad", "L1", "L2 trigger"]),
            P("Right daughterboard", "", "Face buttons, right trackpad, or right stick input dead. Revisions are not interchangeable", 30.0, 0,
              ["Face buttons", "Right trackpad", "R1", "R2 trigger"]),
            P("L1 / R1 bumper button", "", "Bumper does not click or register", 4.0, 0, ["L1", "R1"]),
            P("L1 / R1 tactile switch", "", "Bumper plastic is fine but no input", 1.0, 1, ["L1", "R1"]),
            P("Trigger assembly (L2 / R2)", "", "Broken or sticking trigger", 8.0, 0, ["L2 trigger", "R2 trigger"]),
            P("Back grip button board", "", "Dead L4/L5/R4/R5", 6.0, 0, ["Back grip buttons (L4 L5 R4 R5)"]),
            P("Trackpad (left)", "", "No tracking or no click", 20.0, 0, ["Left trackpad", "Haptics"]),
            P("Trackpad (right)", "", "No tracking or no click", 20.0, 0, ["Right trackpad", "Haptics"]),
            P("Display assembly (OLED)" if oled else "Display assembly (LCD)", "",
              "Cracked, dead, or lined screen", 110.0 if oled else 70.0, 0, ["Display image", "Touchscreen"]),
            P("Fan", "", "Loud whine or not spinning", 25.0, 0, ["Fan and cooling"]),
            P("Thermal paste", "", "Overheating and throttling", 1.0, 0, ["Fan and cooling"]),
            P("SSD (M.2 2230)", "", "Boot failures or storage errors, or a storage upgrade", 40.0, 0,
              ["Boots to SteamOS"]),
            P("Speakers", "", "No or crackling audio", 12.0, 0, ["Speakers"]),
            P("Headphone jack board", "", "No headset audio", 10.0, 0, ["Headphone jack"]),
            P("Power and volume button board", "", "Dead power or volume buttons", 8.0, 0,
              ["Power button", "Volume buttons"]),
            P("Wi-Fi / Bluetooth module", "", "No wireless", 15.0, 1, ["Wi-Fi", "Bluetooth"]),
            P("microSD slot", "", "Cards not detected or slot snapped", 4.0, 1, ["microSD slot"]),
            P("Main board", "", "Donor board swap when the board is beyond repair", 180.0, 0,
              ["Powers on", "Boots to SteamOS"]),
            P("Shell (front, back, or buttons)", "", "Cracked or worn housing", 25.0, 0, ["Shell and cosmetics"]),
        ],
    }


PS5_CONSOLE = {
    "name": "PlayStation 5 console",
    "category": "Console",
    "functions": [
        "Powers on (beeps and light)", "Outputs video over HDMI", "Boots to the home screen",
        "Stays on under load (no overheat shutdown)", "Fan noise normal", "Disc drive accepts and ejects",
        "Disc drive reads games", "Front USB ports", "Rear USB ports", "Wi-Fi", "Bluetooth controllers",
        "LAN port", "Internal storage", "M.2 expansion slot", "Power and eject buttons",
        "Not banned / no error codes", "Shell and cosmetics",
    ],
    "parts": [
        P("HDMI port", "", "No picture, bent pins. The most common PS5 repair", 2.0, 1,
          ["Outputs video over HDMI"]),
        P("HDMI encoder IC", "MN864739", "No picture with a good port, often after a surge through HDMI", 12.0, 1,
          ["Outputs video over HDMI"]),
        P("HDMI line filters and capacitors", "", "Tiny parts behind the port that crack or burn", 1.0, 1,
          ["Outputs video over HDMI"]),
        P("Power supply unit", "", "No power at all. MAINS VOLTAGE: swap the unit, do not open it", 45.0, 0,
          ["Powers on (beeps and light)"]),
        P("Liquid metal reapplication", "", "Overheating shutdowns. Conductive, so contain it carefully", 10.0, 0,
          ["Stays on under load (no overheat shutdown)"]),
        P("Fan", "", "Loud, rattling, or not spinning", 15.0, 0,
          ["Fan noise normal", "Stays on under load (no overheat shutdown)"]),
        P("Disc drive laser", "", "Discs spin but are not read", 20.0, 0, ["Disc drive reads games"]),
        P("Disc drive rollers and motor", "", "Will not take or eject discs", 8.0, 0,
          ["Disc drive accepts and ejects"]),
        P("Disc drive daughterboard", "", "Paired to the main board. Keep the original with its console", 0.0, 0,
          ["Disc drive reads games"]),
        P("USB port (front or rear)", "", "Dead or loose USB port", 2.0, 1, ["Front USB ports", "Rear USB ports"]),
        P("Wi-Fi / Bluetooth module", "", "No Wi-Fi or controllers will not pair wirelessly", 12.0, 1,
          ["Wi-Fi", "Bluetooth controllers"]),
        P("Power and eject button board", "", "Buttons dead", 6.0, 0, ["Power and eject buttons"]),
        P("CMOS battery (CR2032)", "", "Clock resets, some boot and licence errors", 1.0, 0,
          ["Boots to the home screen"]),
        P("Main board", "", "Donor board swap. A board from a banned console stays banned", 150.0, 0,
          ["Powers on (beeps and light)", "Boots to the home screen", "Not banned / no error codes"]),
        P("Shell (side plates, stand)", "", "Cracked or missing covers", 20.0, 0, ["Shell and cosmetics"]),
    ],
}

PS4_CONSOLE = {
    "name": "PlayStation 4 console",
    "category": "Console",
    "functions": [
        "Powers on (beep and light)", "Outputs video over HDMI", "Boots to the home screen",
        "Stays on under load (no overheat shutdown)", "Fan noise normal", "Disc drive accepts and ejects",
        "Disc drive reads games", "USB ports", "Wi-Fi", "Bluetooth controllers", "LAN port",
        "Hard drive", "Power and eject buttons", "Not banned / no error codes", "Shell and cosmetics",
    ],
    "parts": [
        P("HDMI port", "", "No picture or white light with no signal", 2.0, 1, ["Outputs video over HDMI"]),
        P("HDMI encoder IC", "MN86471A / MN864729",
          "No picture with a good port. MN86471A on original models, MN864729 on Slim and Pro", 8.0, 1,
          ["Outputs video over HDMI"]),
        P("Power supply unit", "", "No power at all. MAINS VOLTAGE: swap the unit, do not open it", 25.0, 0,
          ["Powers on (beep and light)"]),
        P("Thermal paste and pads", "", "Overheating shutdowns and jet-engine fan", 3.0, 0,
          ["Stays on under load (no overheat shutdown)", "Fan noise normal"]),
        P("Fan", "", "Loud, rattling, or not spinning", 10.0, 0, ["Fan noise normal"]),
        P("Disc drive laser", "KES-490A / KES-496A", "Discs spin but are not read. Match the drive model", 12.0, 0,
          ["Disc drive reads games"]),
        P("Disc drive rollers and motor", "", "Will not take or eject discs", 6.0, 0,
          ["Disc drive accepts and ejects"]),
        P("Disc drive daughterboard", "", "Paired to the main board. Keep the original with its console", 0.0, 0,
          ["Disc drive reads games"]),
        P("Hard drive (2.5 inch SATA)", "", "Boot loops, safe mode, corrupted database", 20.0, 0,
          ["Hard drive", "Boots to the home screen"]),
        P("USB port", "", "Dead or loose USB port", 2.0, 1, ["USB ports"]),
        P("Wi-Fi / Bluetooth module", "", "No Wi-Fi or controllers will not pair wirelessly", 8.0, 1,
          ["Wi-Fi", "Bluetooth controllers"]),
        P("Power and eject button board", "", "Buttons dead or the console ejects by itself", 5.0, 0,
          ["Power and eject buttons"]),
        P("CMOS battery (CR2032)", "", "Clock resets, licence errors when offline", 1.0, 0,
          ["Boots to the home screen"]),
        P("Main board", "", "Donor board swap. A board from a banned console stays banned", 60.0, 0,
          ["Powers on (beep and light)", "Boots to the home screen", "Not banned / no error codes"]),
        P("Shell (top cover, HDD cover)", "", "Cracked or missing covers", 12.0, 0, ["Shell and cosmetics"]),
    ],
}

CATALOG = [
    DUALSENSE,
    DUALSHOCK4,
    xbox_controller("Xbox Series controller", "USB-C port", "Works wired over USB-C"),
    xbox_controller("Xbox One controller", "Micro-USB port", "Works wired over micro-USB"),
    JOYCON_L,
    JOYCON_R,
    SWITCH_PRO,
    JOYCON2_L,
    JOYCON2_R,
    switch_console("Nintendo Switch (original)", "v1"),
    switch_console("Nintendo Switch Lite", "lite"),
    switch_console("Nintendo Switch OLED", "oled"),
    switch_console("Nintendo Switch 2", "switch2"),
    steam_deck("Steam Deck LCD", oled=False),
    steam_deck("Steam Deck OLED", oled=True),
    PS5_CONSOLE,
    PS4_CONSOLE,
]


def validate():
    """Every function a part claims to fix must exist on that device type."""
    problems = []
    for t in CATALOG:
        names = set(t["functions"])
        if len(names) != len(t["functions"]):
            problems.append("%s: duplicate function names" % t["name"])
        seen = set()
        for part in t["parts"]:
            if part[0] in seen:
                problems.append("%s: duplicate part %s" % (t["name"], part[0]))
            seen.add(part[0])
            for f in part[5]:
                if f not in names:
                    problems.append("%s: part %r fixes unknown function %r" % (t["name"], part[0], f))
    return problems


if __name__ == "__main__":
    for p in validate():
        print(p)
    print("%d device types, %d parts" % (len(CATALOG), sum(len(t["parts"]) for t in CATALOG)))
