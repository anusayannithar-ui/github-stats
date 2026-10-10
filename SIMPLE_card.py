def repo_card(repo_name: str, stars: int, forks: int, language: str) -> str:
    #simple card --start, improve later
    
    width = 420
    height = 120

    svg = f"""
    <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
      <rect width="{width}" height="{height}" rx="14" fill="#0d1117"/>
      <rect x="18" y="18" width="384" height="84" rx="12" fill="#161b22" stroke="#30363d"/>
      <text x="32" y="48" fill="#c9d1d9" font-family="Arial, sans-serif" font-size="22" font-weight="700">
        {repo_name}
      </text>
      <text x="32" y="72" fill="#8b949e" font-family="Arial, sans-serif" font-size="14">
        {language}
      </text>
      <text x="32" y="95" fill="#c9d1d9" font-family="Arial, sans-serif" font-size="14">
        ⭐ {stars}    🔀 {forks}
      </text>
    </svg>
    """

    return svg.strip()


if __name__ == "__main__":
    card = repo_card("github-stats", 120, 34, "Python")
    with open("repo_card.svg", "w", encoding="utf-8") as f:
        f.write(card)
    print("Saved repo_card.svg")
