use criterion::{black_box, criterion_group, criterion_main, Criterion};
use gitripper::parse_github_url;

fn benchmark_parse_https_url(c: &mut Criterion) {
    c.bench_function("parse_https_url", |b| {
        b.iter(|| parse_github_url(black_box("https://github.com/user/repo")))
    });
}

fn benchmark_parse_https_url_with_git(c: &mut Criterion) {
    c.bench_function("parse_https_url_with_git", |b| {
        b.iter(|| {
            parse_github_url(black_box("https://github.com/user/repo.git"))
        })
    });
}

fn benchmark_parse_ssh_url(c: &mut Criterion) {
    c.bench_function("parse_ssh_url", |b| {
        b.iter(|| parse_github_url(black_box("git@github.com:user/repo.git")))
    });
}

fn benchmark_parse_ssh_protocol_url(c: &mut Criterion) {
    c.bench_function("parse_ssh_protocol_url", |b| {
        b.iter(|| {
            parse_github_url(black_box("ssh://git@github.com/user/repo.git"))
        })
    });
}

fn benchmark_parse_url_with_whitespace(c: &mut Criterion) {
    c.bench_function("parse_url_with_whitespace", |b| {
        b.iter(|| {
            parse_github_url(black_box("  https://github.com/user/repo  "))
        })
    });
}

fn benchmark_parse_invalid_url(c: &mut Criterion) {
    c.bench_function("parse_invalid_url", |b| {
        b.iter(|| parse_github_url(black_box("https://example.com/user/repo")))
    });
}

criterion_group!(
    benches,
    benchmark_parse_https_url,
    benchmark_parse_https_url_with_git,
    benchmark_parse_ssh_url,
    benchmark_parse_ssh_protocol_url,
    benchmark_parse_url_with_whitespace,
    benchmark_parse_invalid_url,
);

criterion_main!(benches);
