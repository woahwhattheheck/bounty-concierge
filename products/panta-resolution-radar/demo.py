"""Synthetic Panta-shaped demo data. Never used as a live fallback."""
import hashlib


def address(number):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    n = int.from_bytes(hashlib.sha256(f'NOT-LIVE-RADAR-DEMO-{number}'.encode()).digest(), 'big')
    out = ''
    while n:
        n, r = divmod(n, 58)
        out = alphabet[r] + out
    return out


def demo_items(now):
    rows = [
        ('Will the open-source observatory publish its October dataset?', 'science', 'secondary', -10800, -14400, '8120.340000', False),
        ('Will Harbor City open its new electric ferry route?', 'world', 'primary', 7200, 3600, '3215.00', False),
        ('Will the community chess final finish before midnight?', 'sports', 'primary', 14400, 10800, '975.125', False),
        ('Will Project Cedar ship its public beta this week?', 'crypto', 'secondary', 72000, 68400, '12000.00', False),
        ('Will the independent game launch its next expansion?', 'entertainment', 'primary', 172800, 169200, '840.75', False),
        ('Will the metro publish a new accessibility audit?', 'world', 'primary', None, -3600, None, False),
        ('Did the weekend robotics showcase reach its target?', 'science', 'resolved', -86400, -90000, '6550.90', True),
        ('Will the outdoor film screening run on schedule?', 'entertainment', 'cancelled', -7200, -10800, '50.00', False),
    ]
    items = []
    for i, (title, category, phase, resolution, end, volume, resolved) in enumerate(rows):
        items.append(dict(marketId=address(i), title=title, category=category, phase=phase,
            description='Synthetic demonstration only. This is not a real Panta market, oracle statement or investment opportunity.',
            marketType='standard', startTime=now - 172800, endTime=now + end,
            resolutionTime=None if resolution is None else now + resolution,
            region='Global', resolved=resolved, status='open' if phase in ('primary', 'secondary') else phase,
            volumeUsdc=volume, totalVolumeUsdc=volume, yesPrice=None, noPrice=None,
            primaryYesPrice=None, primaryNoPrice=None, secondaryYesPrice=None, secondaryNoPrice=None,
            oracle='Demonstration source — no oracle queried', createdByPartner=False))
    return items
