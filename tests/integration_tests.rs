use gitripper::parse_github_url;

#[test]
fn integration_parse_various_github_urls() {
    let test_cases = vec![
        ("https://github.com/torvalds/linux", "torvalds", "linux"),
        ("https://github.com/rust-lang/rust.git", "rust-lang", "rust"),
        ("git@github.com:golang/go", "golang", "go"),
        ("ssh://git@github.com/python/cpython.git", "python", "cpython"),
    ];

    for (url, expected_owner, expected_repo) in test_cases {
        let (owner, repo) = parse_github_url(url)
            .unwrap_or_else(|_| panic!("Failed to parse URL: {}", url));

        assert_eq!(owner, expected_owner);
        assert_eq!(repo, expected_repo);
    }
}

#[test]
fn integration_reject_invalid_urls() {
    let invalid_urls = vec![
        "",
        "not-a-url",
        "https://example.com/user/repo",
        "https://gitlab.com/user/repo",
        "http://github.com",
    ];

    for url in invalid_urls {
        assert!(
            parse_github_url(url).is_err(),
            "Expected URL '{}' to be invalid",
            url
        );
    }
}

#[test]
fn integration_parse_github_url_with_special_chars() {
    let urls = vec![
        ("https://github.com/user-name/repo-name", "user-name", "repo-name"),
        ("https://github.com/user_123/repo_test", "user_123", "repo_test"),
    ];

    for (url, expected_owner, expected_repo) in urls {
        let (owner, repo) = parse_github_url(url).unwrap();
        assert_eq!(owner, expected_owner);
        assert_eq!(repo, expected_repo);
    }
}
