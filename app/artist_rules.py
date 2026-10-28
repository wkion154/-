"""Conservative structured-claim rules; names alone never establish identity.
Wikidata IDs are explicit vocabulary references, not face identification.
Rules are inactive until the provider policy review gate is satisfied.
"""
import re

# Well-defined Wikidata vocabulary, to be confirmed in the live-provider audit.
HUMAN='Q5'
FEMALE='Q6581072'
GIRL_GROUP='Q641066'
K_POP='Q213665'


def referenced_values(raw,prop):
    return [claim.get('mainsnak',{}).get('datavalue',{}).get('value')
            for claim in raw.get('claims',{}).get(prop,[])
            if claim.get('references') and claim.get('rank')!='deprecated'
            and claim.get('mainsnak',{}).get('snaktype')=='value']


def automatic_profile(candidate):
    raw=candidate.get('raw',{})
    if not candidate.get('category') or candidate.get('name')==candidate.get('external_id'):
        return None
    values=lambda prop:{x.get('id') for x in referenced_values(raw,prop) if isinstance(x,dict) and x.get('id')}
    if K_POP not in values('P136'):return None
    if candidate['kind']=='group':
        if GIRL_GROUP not in values('P31'):return None
        dates=referenced_values(raw,'P571')
        profile=dict(group_kind='girl')
    else:
        if HUMAN not in values('P31') or values('P21')!={FEMALE}:return None
        dates=referenced_values(raw,'P2031')
        # Female artist does not imply solo activity. That remains separately reviewed.
        profile=dict(gender='female',solo=False)
    years=[]
    for value in dates:
        match=re.match(r'^\+(\d{4})-',value.get('time','')) if isinstance(value,dict) else None
        if match:years.append(int(match.group(1)))
    # Operational discovery window, not an assertion of an official generation boundary.
    if not years or min(years)<2012:return None
    return profile
