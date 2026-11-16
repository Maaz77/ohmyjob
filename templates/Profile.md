<!-- OHMYJOB-TEMPLATE: replace the mock data with yours, then delete this line -->
# Fit Preferences

The screening workers judge every posting against these three fields FIRST: a dealbreaker or a clear seniority mismatch rejects a job, and "What I'm looking for" is the main fit criterion. Write them in your own words, concretely. Replace the mock answers.

- **Seniority / experience level:** Mid-level, about six years of experience. Not looking for director, head-of or intern roles.
- **What I'm looking for:** Analyst or coordinator roles in logistics, supply chain or operations, where I build forecasts and KPI reports that planners act on. Based in Berlin, hybrid with up to two office days a week, or fully remote. Open to relocating within the EU for the right role.
- **Dealbreakers:** Night shifts, on-call rotas, and roles that are mostly sales.

# Resume

Your master resume: every fact the tailored resumes may use, with numbers where you have them. Tailoring uses this section as its only source of facts, so write everything true and nothing else. Run `/ohmyjob:master-resume` to draft this section from your resume files, then revise it. Replace each guidance line below with your own text; delete the sections you do not need.

- Name: <First> <Last>
- Email: your email address
- Phone: your phone number
- Address: your city and country
- Links: your homepage, GitHub and LinkedIn addresses

## Summary

Two or three sentences on who you are professionally, your field and your strongest skills.

## Work Experience

One block per role, newest first: `### <Role> | <Company> | <Location> | <Dates>`, then one `- bullet` per achievement, with numbers where you have them.

## Education

One block per degree: `### <Degree> | <School> | <Location> | <Dates>`, then `- bullet` lines for a thesis, grade or honours.

## Skills

One line per group: `- **<Label>:** skill, skill, skill`.

## Honors & Awards

One line per award: `- <Award> | <Event or giver> | <Location> | <Date>`.

## Certificates

One line per certificate: `- <Certificate> | <Issuer> | <Credential id, may be empty> | <Date>`.

## Extracurricular Activity

Volunteering and clubs: `### <Role> | <Organisation> | <Location> | <Dates>`, then `- bullet` lines.

## Presentation

Talks you gave: `### <Talk> | <Event> | <Location> | <Date>`, then `- bullet` lines.

## Writing

Articles and publications: `### <Role> | <Publication> | <Location> | <Dates>`, then `- bullet` lines.

## Program Committees

One line per committee: `- <Position> | <Committee> | <Location> | <Date>`.

# Follow Up Questions

Answers the application workers reuse on forms. One numbered line per item: `1. Question? Answer.` Keep the heading exact. Replace the mock answers with yours; apply-jobs asks you any question that is missing and saves the answer here, with the next number. The list holds only questions that most forms ask: the worker answers ordinary consents and form questions itself and asks you the rest. The block after the "Tech roles" line holds questions that tech-job forms ask: not applying to tech jobs? Delete that whole block.

1. What are your salary expectations? 60,000 - 66,000 EUR gross per year, negotiable, not below 58,000. When the form needs one number: 62,000.
2. What is your current annual salary? 58,000 EUR gross per year.
3. What is your notice period? Two months to the end of a calendar month.
4. What is your earliest start date? The first of the month after the notice period ends.
5. Are you legally authorized to work in <country>? Yes in Germany and the Netherlands (EU citizen). No elsewhere without a visa.
6. Will you now or in the future require sponsorship for an employment visa? Not in the EU. Yes in <country> outside the EU.
7. What is your current visa or immigration status in <country>? EU citizen in every EU country; no status elsewhere. On a list without that choice: Other.
8. What is your citizenship or nationality? German.
9. What work arrangement do you want (onsite, hybrid, remote)? Hybrid or remote, up to 2 office days a week.
10. What job type do you want (permanent, part-time, contract)? A permanent contract.
11. Are you willing to relocate? Yes, within the EU.
12. What is your preferred location or office? The location of the job. When the form lists several offices, the one nearest to Berlin; when it offers only one, that one.
13. Are you willing to travel for work? Yes, up to 20 percent of the time.
14. Which languages do you speak, and how well? German native, English fluent (C1), Spanish basic (B1).
15. What is your highest level of education? Bachelor's degree.
16. How many years of professional experience do you have in total? 6.
17. How many years of professional experience do you have with <skill>? 6 for SQL and Excel, 3 for Python, 0 for anything not listed in Resume.
18. Do you have experience with <tool or technology>? Answer Yes only for what is written in Resume, otherwise No.
19. Have you used <the company's product>, even in a personal project? No.
20. Have you worked for <company> before, directly or as a contractor, applied there before, or do you have relatives or acquaintances there? No.
21. Are you at least 18 years old? Yes.
22. Do you have a valid driver's license? Yes, EU class B.
23. Are you willing to undergo a background check? Yes.
24. Do you have any convictions, cautions, reprimands, warnings or court cases? No.
25. Do you have a health condition or disability that needs reasonable adjustments? No.
26. Can you provide references from previous employers? Yes, on request.
27. Do you consent to AI tools (including large language models) being used in the recruitment process? Yes.
28. What is your home address (street, postal code, city, country)? Musterstrasse 12, 10115 Berlin, Germany.
29. What goes in a second address line, when the form requires one? Leave it empty.
30. Is your phone number a mobile or a landline? Mobile number.
31. Date of birth? 1 January 1990.
32. Title (Mr., Ms., Mx., none)? Leave empty.
33. Preferred pronouns? Leave empty when optional; otherwise they/them.

Tech roles — the questions below are asked on tech-job forms. Not applying to tech jobs? Delete every line below this one.

34. Which services or products of <cloud or software platform> have you worked with (a multiple-choice list)? Only those named in Resume; when none is listed, Other.
35. On a scale of 1-10, how proficient are you with <skill or framework>? 8 for the main Resume skills, 6 for secondary ones, 1 for anything else.
36. Have you built and shipped a system end to end, from design to production? Yes.
37. Do you have experience with on-call rotations? No.

# Work Authorization

Screening drops a posting when it says outright that it offers no visa sponsorship, needs the right to work, citizenship, permanent residency or a security clearance, and the line below for the job's country says you do not meet that. A posting that says nothing about it is never dropped. A country with no line here is never dropped either: the job is kept and its Gaps say it was not checked. Write one line per country, `- <Country>: <status>. your own words`, where the status starts the line and is exactly one of `citizen`, `permanent resident`, `authorized` (you may work there without sponsorship) or `needs sponsorship`. A line `- EU: ...` covers every EU country that has no line of its own. The last line is `Security clearance: none`, or names the clearance you hold. Replace the mock answers.

- EU: citizen. I am an EU citizen, so I need no visa in any EU country.
- United Kingdom: needs sponsorship. I would need an employer visa there.
- United States: needs sponsorship. I would need an employer visa there.
- Security clearance: none.

# Languages

Screening drops a posting that requires a language you did not list here, and a freehire posting written in a language you did not list is dropped while the jobs are collected. This list is read as complete: a language that is not here is one you do not have, so list every language you speak, including the ones you only half speak. There is no level test: a language named here counts as met, whatever level the posting asks for and whatever level your line claims, so the words after the colon are for you and for the forms. Write one line per language, `- <Language>: your own words`. A profile without this section is never filtered on language. Keep Follow Up Questions item 14 in agreement with this list. Replace the mock answers.

- German: native. Mother tongue.
- English: fluent, C1. I have worked in English for six years.
- Spanish: basic, B1. I follow a conversation but cannot run a meeting.
