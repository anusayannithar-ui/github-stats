#!/usr/bin/python3

import asyncio
import os
from typing import Any, Dict, List, Optional, Set, Tuple, cast

import aiohttp
import requests
from loguru import logger


###############################################################################
# Main Classes
###############################################################################


class Queries(object):
    """
    Class with functions to query the GitHub GraphQL (v4) API and the REST (v3)
    API. Also includes functions to dynamically generate GraphQL queries.
    """

    def __init__(
        self,
        username: str,
        access_token: str,
        session: aiohttp.ClientSession,
        max_connections: int = 10,
    ):
        self.username = username
        self.access_token = access_token
        self.session = session
        self.semaphore = asyncio.Semaphore(max_connections)

    async def query(self, generated_query: str) -> Dict:
        """
        Make a request to the GraphQL API using the authentication token from
        the environment.
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
        }

        try:
            async with self.semaphore:
                async with self.session.post(
                    "https://api.github.com/graphql",
                    headers=headers,
                    json={"query": generated_query},
                ) as response:
                    result = await response.json()
                    if result is not None:
                        return result
        except Exception:
            logger.exception("aiohttp failed for GraphQL query")

        try:

            def fallback_request() -> Dict:
                resp = requests.post(
                    "https://api.github.com/graphql",
                    headers=headers,
                    json={"query": generated_query},
                )
                return resp.json()

            return await asyncio.to_thread(fallback_request)
        except Exception:
            logger.exception("Fallback GraphQL request also failed.")
            return {}

    async def query_rest(self, path: str, params: Optional[Dict] = None) -> Dict:
        """
        Make a request to the REST API.
        """
        if params is None:
            params = {}

        if path.startswith("/"):
            path = path[1:]

        headers = {
            "Authorization": f"token {self.access_token}",
        }

        for _ in range(60):
            try:
                async with self.semaphore:
                    async with self.session.get(
                        f"https://api.github.com/{path}",
                        headers=headers,
                        params=params,
                    ) as response:
                        if response.status == 202:
                            logger.info("A path returned 202. Retrying...")
                            await asyncio.sleep(2)
                            continue

                        result = await response.json()
                        if result is not None:
                            return result
            except Exception:
                logger.exception("aiohttp failed for rest query")

            try:

                def fallback_request():
                    resp = requests.get(
                        f"https://api.github.com/{path}",
                        headers=headers,
                        params=params,
                    )
                    return resp

                response = await asyncio.to_thread(fallback_request)

                if response.status_code == 202:
                    logger.info("A path returned 202. Retrying...")
                    await asyncio.sleep(2)
                    continue
                elif response.status_code == 200:
                    return response.json()
            except Exception:
                logger.exception("Fallback requests call also failed.")
                return {}

        print("There were too many 202s. Data for this repository will be incomplete.")
        return {}

    @staticmethod
    def repos_overview(
        contrib_cursor: Optional[str] = None, owned_cursor: Optional[str] = None
    ) -> str:
        """
        Return a GraphQL query for user repository overview.
        """
        return f"""{{
  viewer {{
    login,
    name,
    repositories(
        first: 100,
        orderBy: {{
            field: UPDATED_AT,
            direction: DESC
        }},
        isFork: false,
        after: {"null" if owned_cursor is None else '"' + owned_cursor + '"'}
    ) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        nameWithOwner
        stargazers {{
          totalCount
        }}
        forkCount
        languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
    repositoriesContributedTo(
        first: 100,
        includeUserRepositories: false,
        orderBy: {{
            field: UPDATED_AT,
            direction: DESC
        }},
        contributionTypes: [
            COMMIT,
            PULL_REQUEST,
            REPOSITORY,
            PULL_REQUEST_REVIEW
        ],
        after: {"null" if contrib_cursor is None else '"' + contrib_cursor + '"'}
    ) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        nameWithOwner
        stargazers {{
          totalCount
        }}
        forkCount
        languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
  }}
}}"""

    @staticmethod
    def contrib_years() -> str:
        """
        Return GraphQL query to get all years the user has contributed.
        """
        return """
query {
  viewer {
    contributionsCollection {
      contributionYears
    }
  }
}
"""

    @staticmethod
    def contribs_by_year(year: str) -> str:
        """
        Return GraphQL query fragment for a single year.
        """
        return f"""
    year{year}: contributionsCollection(
        from: "{year}-01-01T00:00:00Z",
        to: "{int(year) + 1}-01-01T00:00:00Z"
    ) {{
      contributionCalendar {{
        totalContributions
      }}
    }}
"""

    @classmethod
    def all_contribs(cls, years: List[str]) -> str:
        """
        Return GraphQL query for all years.
        """
        by_years = "\n".join(map(cls.contribs_by_year, years))
        return f"""
query {{
  viewer {{
    {by_years}
  }}
}}
"""

    @staticmethod
    def _safe_total_count(repo: Dict[str, Any]) -> int:
        stargazers = repo.get("stargazers")
        if isinstance(stargazers, dict):
            return int(stargazers.get("totalCount", 0))
        return 0


class Stats(object):
    """
    Retrieve and store statistics about GitHub usage.
    """

    def __init__(
        self,
        username: str,
        access_token: str,
        session: aiohttp.ClientSession,
        exclude_repos: Optional[Set[str]] = None,
        exclude_langs: Optional[Set[str]] = None,
        ignore_forked_repos: bool = False,
    ):
        self.username = username
        self._ignore_forked_repos = ignore_forked_repos
        self._exclude_repos = set() if exclude_repos is None else exclude_repos
        self._exclude_langs = set() if exclude_langs is None else exclude_langs
        self.queries = Queries(username, access_token, session)

        self._name: Optional[str] = None
        self._stargazers: Optional[int] = None
        self._forks: Optional[int] = None
        self._total_contributions: Optional[int] = None
        self._languages: Optional[Dict[str, Any]] = None
        self._repos: Optional[Set[str]] = None
        self._lines_changed: Optional[Tuple[int, int]] = None
        self._views: Optional[int] = None

    async def to_str(self) -> str:
        """
        Return a human-readable summary of available statistics.
        """
        languages = await self.languages_proportional
        formatted_languages = "\n  - ".join(
            [f"{k}: {v:0.4f}%" for k, v in languages.items()]
        )
        lines_changed = await self.lines_changed
        return f"""Name: {await self.name}
Stargazers: {await self.stargazers:,}
Forks: {await self.forks:,}
All-time contributions: {await self.total_contributions:,}
Repositories with contributions: {len(await self.repos)}
Lines of code added: {lines_changed[0]:,}
Lines of code deleted: {lines_changed[1]:,}
Lines of code changed: {lines_changed[0] + lines_changed[1]:,}
Project page views: {await self.views:,}
Languages:
  - {formatted_languages}"""

    async def get_stats(self) -> None:
        """
        Get lots of summary statistics using one big query. Sets many attributes.
        """
        self._stargazers = 0
        self._forks = 0
        self._languages = {}
        self._repos = set()

        exclude_langs_lower = {x.lower() for x in self._exclude_langs}

        next_owned = None
        next_contrib = None

        while True:
            raw_results = await self.queries.query(
                Queries.repos_overview(
                    owned_cursor=next_owned, contrib_cursor=next_contrib
                )
            )
            raw_results = raw_results if raw_results is not None else {}

            viewer = raw_results.get("data", {}).get("viewer", {})

            self._name = viewer.get("name") or viewer.get("login") or "No Name"

            contrib_repos = viewer.get("repositoriesContributedTo", {})
            owned_repos = viewer.get("repositories", {})

            repos = owned_repos.get("nodes", [])
            if not self._ignore_forked_repos:
                repos += contrib_repos.get("nodes", [])

            for repo in repos:
                if repo is None:
                    continue

                name = repo.get("nameWithOwner")
                if name in self._repos or name in self._exclude_repos:
                    continue

                self._repos.add(name)
                self._stargazers += self._safe_total_count(repo)
                self._forks += int(repo.get("forkCount", 0))

                for lang in repo.get("languages", {}).get("edges", []):
                    lang_name = lang.get("node", {}).get("name", "Other")
                    if lang_name.lower() in exclude_langs_lower:
                        continue

                    languages = await self.languages
                    if lang_name in languages:
                        languages[lang_name]["size"] += lang.get("size", 0)
                        languages[lang_name]["occurrences"] += 1
                    else:
                        languages[lang_name] = {
                            "size": lang.get("size", 0),
                            "occurrences": 1,
                            "color": lang.get("node", {}).get("color"),
                        }

            if owned_repos.get("pageInfo", {}).get(
                "hasNextPage", False
            ) or contrib_repos.get("pageInfo", {}).get("hasNextPage", False):
                next_owned = owned_repos.get("pageInfo", {}).get(
                    "endCursor", next_owned
                )
                next_contrib = contrib_repos.get("pageInfo", {}).get(
                    "endCursor", next_contrib
                )
            else:
                break

        langs_total = sum(v.get("size", 0) for v in self._languages.values())
        if langs_total > 0:
            for _, value in self._languages.items():
                value["prop"] = 100 * (value.get("size", 0) / langs_total)
        else:
            for _, value in self._languages.items():
                value["prop"] = 0.0

    @property
    async def name(self) -> str:
        """
        Return the GitHub user's name.
        """
        if self._name is not None:
            return self._name
        await self.get_stats()
        assert self._name is not None
        return self._name

    @property
    async def stargazers(self) -> int:
        """
        Return total number of stargazers on user's repos.
        """
        if self._stargazers is not None:
            return self._stargazers
        await self.get_stats()
        assert self._stargazers is not None
        return self._stargazers

    @property
    async def forks(self) -> int:
        """
        Return total number of forks on user's repos.
        """
        if self._forks is not None:
            return self._forks
        await self.get_stats()
        assert self._forks is not None
        return self._forks

    @property
    async def languages(self) -> Dict:
        """
        Return summary of languages used by the user.
        """
        if self._languages is not None:
            return self._languages
        await self.get_stats()
        assert self._languages is not None
        return self._languages

    @property
    async def languages_proportional(self) -> Dict:
        """
        Return languages with proportional usage.
        """
        if self._languages is None:
            await self.get_stats()
            assert self._languages is not None

        return {k: v.get("prop", 0) for (k, v) in self._languages.items()}

    @property
    async def repos(self) -> Set[str]:
        """
        Return names of the user's repos.
        """
        if self._repos is not None:
            return self._repos
        await self.get_stats()
        assert self._repos is not None
        return self._repos

    @property
    async def total_contributions(self) -> int:
        """
        Return the total number of contributions as defined by GitHub.
        """
        if self._total_contributions is not None:
            return self._total_contributions

        self._total_contributions = 0
        years = (
            (await self.queries.query(Queries.contrib_years()))
            .get("data", {})
            .get("viewer", {})
            .get("contributionsCollection", {})
            .get("contributionYears", [])
        )

        by_year = (
            (await self.queries.query(Queries.all_contribs(years)))
            .get("data", {})
            .get("viewer", {})
            .values()
        )

        for year in by_year:
            self._total_contributions += year.get("contributionCalendar", {}).get(
                "totalContributions", 0
            )

        return cast(int, self._total_contributions)

    @property
    async def lines_changed(self) -> Tuple[int, int]:
        """
        Return total lines added and deleted by the user.
        """
        if self._lines_changed is not None:
            return self._lines_changed

        additions = 0
        deletions = 0

        for repo in await self.repos:
            r = await self.queries.query_rest(f"/repos/{repo}/stats/contributors")
            if not isinstance(r, list):
                continue

            for author_obj in r:
                if not isinstance(author_obj, dict):
                    continue

                author = author_obj.get("author", {})
                if not isinstance(author, dict):
                    continue

                if author.get("login", "") != self.username:
                    continue

                for week in author_obj.get("weeks", []):
                    additions += week.get("a", 0)
                    deletions += week.get("d", 0)

        self._lines_changed = (additions, deletions)
        return self._lines_changed

    @property
    async def views(self) -> int:
        """
        Return total page views for the user's projects over the last 14 days.
        """
        if self._views is not None:
            return self._views

        total = 0
        for repo in await self.repos:
            r = await self.queries.query_rest(f"/repos/{repo}/traffic/views")
            if not isinstance(r, dict):
                continue

            for view in r.get("views", []):
                total += view.get("count", 0)

        self._views = total
        return self._views


###############################################################################
# Main Function
###############################################################################


async def main() -> None:
    """
    Used mostly for testing; this module is not usually run standalone.
    """
    access_token = os.getenv("ACCESS_TOKEN")
    user = os.getenv("GITHUB_ACTOR")

    if access_token is None or user is None:
        raise RuntimeError(
            "ACCESS_TOKEN and GITHUB_ACTOR environment variables cannot be None!"
        )

    async with aiohttp.ClientSession() as session:
        s = Stats(user, access_token, session)
        print(await s.to_str())


if __name__ == "__main__":
    asyncio.run(main())
