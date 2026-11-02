## Section 5 – Data Analysis and Interpretation

For **Section 5**, the following analytical questions are appropriate for the dimensional model:

- How has the number of releases evolved over time, by year and title type?
- What are the most frequent genre combinations?
- How does the average known runtime vary by title type and decade?
- How has the composition of the cumulative catalog changed over time?
- What percentage of titles have missing runtime information?

The analysis should distinguish between **observed results** and possible explanations. For example, an increase in recorded titles may reflect changes in dataset coverage rather than solely an increase in audiovisual production.

## Section 6 – Critical Discussion

The discussion should address both the advantages and limitations of the dimensional model.

**Advantages:**
- Consistent and standardized measures.
- Shared dimensions across fact tables.
- Simpler analytical queries and reporting.

**Limitations:**
- Additional storage requirements for periodic snapshots.
- Need to maintain and update ETL processes.
- Loss of certain attributes from the original dataset.
- Limited flexibility when analysing individual genres.

Since the data source is a TSV extract, avoid making assumptions about the internal operational database architecture of IMDb.

## Current Progress and Priorities

**Section 4 is already well developed. The main remaining task is Section 5.**

Once the analytical queries have been implemented and their results interpreted, writing the critical discussion and final conclusion will become significantly easier.