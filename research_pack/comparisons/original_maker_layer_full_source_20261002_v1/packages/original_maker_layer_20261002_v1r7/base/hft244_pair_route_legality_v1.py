"""Pure native-book self-cross preflight, not an economic admission score."""
def crossing_owners(side,contract_price,reservations):
    if side not in ('UP','DOWN'):raise ValueError('unknown side')
    # Match the frozen native_order mapping, including two-decimal tick rounding.
    native_price=round(contract_price if side=='UP' else 1-contract_price,2)
    hits=[]
    for r in reservations:
        if r['side']==side:continue
        if r['side'] not in ('UP','DOWN'):raise ValueError('unknown owner side')
        opposite_native=round(r['price'] if r['side']=='UP' else 1-r['price'],2)
        cross=native_price>=opposite_native if side=='UP' else native_price<=opposite_native
        if cross:hits.append(r['key'])
    # Input must be current slot reservations: NONE/submit/cancel-pending owners
    # remain included. Caller may remove only confirmed-terminal/released owners.
    return hits
