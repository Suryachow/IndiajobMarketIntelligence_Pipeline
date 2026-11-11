# Company Coverage Summary

_Extraction date: 2026-11-11_

`Portal Job Count` is the number of active India-scoped postings the employer's own career portal advertises (the denominator). `Jobs Extracted` is the number successfully retrieved and validated. Where the portal total cannot be established it is reported as `unknown` rather than estimated.

| Company | Industry | Platform | Portal Job Count (India) | Jobs Extracted | Coverage % | Status |
|---|---|---|---:|---:|---:|---|
| Amazon | E-commerce & Cloud Computing | amazon | 2102 | 2103 | 100.0% | OK |
| Barclays | Banking & Financial Services | workday | 282 | 282 | 100.0% | OK |
| Verizon | Telecommunications | workday | 31 | 31 | 100.0% | OK |
| NVIDIA | Semiconductors | workday | 248 | 248 | 100.0% | OK |
| Cisco | Technology Hardware & Networking | workday | 297 | 297 | 100.0% | OK |
| Pfizer | Pharmaceuticals & Life Sciences | workday | 27 | 27 | 100.0% | OK |
| Salesforce | Enterprise Software | workday | 96 | 96 | 100.0% | OK |
| Sutherland | IT Services & Business Process Management | smartrecruiters | 52 | 52 | 100.0% | OK |
| Groww | Financial Technology (Fintech) | greenhouse | 9 | 9 | 100.0% | OK |
| Meesho | E-commerce & Internet | lever | 55 | 55 | 100.0% | OK |
| Tata Consultancy Services | IT Services & Consulting | tcs | 1436 | 1426 | 99.3% | OK |
| **Total** | | | **4635** | **4626** | **99.8%** | |

## Per-company notes

### Amazon (amazon)
- Careers portal: https://www.amazon.jobs/en/
- Portal total scope: amazon.jobs search hits for country=IND
- Listed 2103 postings (portal reports 2102)

### Barclays (workday)
- Careers portal: https://barclays.wd3.myworkdayjobs.com/External_Career_Site_Barclays
- Portal total scope: postings matching India in locationsText
- No India facet discovered; falling back to full scan filtered on locationsText
- Listed 282 India postings; fetching details

### Verizon (workday)
- Careers portal: https://verizon.wd12.myworkdayjobs.com/verizon-careers
- Portal total scope: Workday India facet total
- India facet strategy: 'locationMainGroup/Locations' (facet='locations', 3 value(s))
- Listed 31 India postings; fetching details

### NVIDIA (workday)
- Careers portal: https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
- Portal total scope: Workday India facet total
- India facet strategy: 'locationMainGroup/Sites' (facet='locations', 6 value(s))
- Listed 248 India postings; fetching details

### Cisco (workday)
- Careers portal: https://cisco.wd5.myworkdayjobs.com/Cisco_Careers
- Portal total scope: Workday India facet total
- India facet strategy: 'locationMainGroup/Locations' (facet='locations', 8 value(s))
- Listed 297 India postings; fetching details

### Pfizer (workday)
- Careers portal: https://pfizer.wd1.myworkdayjobs.com/PfizerCareers
- Portal total scope: Workday India facet total
- India facet strategy: 'country-facet' (facet='Location_Country', 1 value(s))
- Listed 27 India postings; fetching details

### Salesforce (workday)
- Careers portal: https://salesforce.wd12.myworkdayjobs.com/External_Career_Site
- Portal total scope: Workday India facet total
- India facet strategy: 'country-facet' (facet='CF_-_REC_-_LRV_-_Job_Posting_Anchor_-_Country_from_Job_Posting_Location_Extended', 1 value(s))
- Listed 96 India postings; fetching details

### Sutherland (smartrecruiters)
- Careers portal: https://jobs.smartrecruiters.com/Sutherland
- Portal total scope: India postings found while scanning all 437 portal postings
- Scanned 437 postings; 52 located in India

### Groww (greenhouse)
- Careers portal: https://boards.greenhouse.io/groww
- Portal total scope: India postings returned by the full board payload
- Board returned 9 postings; 9 located in India

### Meesho (lever)
- Careers portal: https://jobs.lever.co/meesho
- Portal total scope: India postings returned by the full postings payload
- API returned 55 postings; 55 located in India

### Tata Consultancy Services (tcs)
- Careers portal: https://ibegin.tcsapps.com/candidate/next/en-IN/landing
- Portal total scope: India postings returned by the iBegin search API (regular + walk-in)
- iBegin index returned 1436 posting(s) across 29 page(s)
- skipped 10 non-India posting(s)

## Run statistics

- Duplicate records removed: 30 (exact-hash=29, business-key=1, fuzzy=0)
- HTTP requests issued: 0
- Cache hits: 1139
- Requests that exhausted retries: 0
