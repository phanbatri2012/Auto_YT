import re

with open('debug_html.txt', 'r', encoding='utf-8') as f:
    html = f.read()

buttons = re.findall(r'<button[^>]*>', html)
for b in buttons:
    testid_match = re.search(r'data-testid=[\"\'\\]([^\"\'\\]+)[\"\'\\]', b)
    testid = testid_match.group(1) if testid_match else ''
    
    aria_match = re.search(r'aria-label=[\"\'\\]([^\"\'\\]+)[\"\'\\]', b)
    aria = aria_match.group(1) if aria_match else ''
    
    class_match = re.search(r'class=[\"\'\\]([^\"\'\\]+)[\"\'\\]', b)
    classes = class_match.group(1) if class_match else ''
    
    dis = 'disabled' in b
    if 'send' in aria.lower() or 'stop' in aria.lower() or 'voice' in aria.lower() or 'message' in aria.lower():
        print(f'Button - testid: {testid}, aria-label: {aria}, class: {classes}, disabled: {dis}')
        print(f'Raw: {b}')
