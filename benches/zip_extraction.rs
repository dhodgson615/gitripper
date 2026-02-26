use std::path::PathBuf;

use criterion::{
    black_box, criterion_group, criterion_main, BenchmarkId, Criterion,
};
use gitripper::{write_entry, MemEntry};

fn create_test_entry(size: usize, name: &str) -> MemEntry {
    MemEntry {
        rel_path:   PathBuf::from(name),
        is_dir:     false,
        _data_size: size as u64,
        unix_mode:  Some(0o644),
        _file_idx:  0,
        data:       vec![42; size], // Fill with test data
    }
}

fn benchmark_write_small_file(c: &mut Criterion) {
    c.bench_function("write_small_file", |b| {
        b.iter_with_setup(
            || {
                let temp_dir = tempfile::tempdir().unwrap();
                (temp_dir.path().to_path_buf(), temp_dir)
            },
            |(path, _temp_dir)| {
                let entry = create_test_entry(1024, "small.txt");
                let _ = write_entry(black_box(&entry), black_box(&path));
            },
        )
    });
}

fn benchmark_write_medium_file(c: &mut Criterion) {
    c.bench_function("write_medium_file", |b| {
        b.iter_with_setup(
            || {
                let temp_dir = tempfile::tempdir().unwrap();
                (temp_dir.path().to_path_buf(), temp_dir)
            },
            |(path, _temp_dir)| {
                let entry = create_test_entry(1024 * 1024, "medium.bin"); // 1 MB
                let _ = write_entry(black_box(&entry), black_box(&path));
            },
        )
    });
}

fn benchmark_write_large_file(c: &mut Criterion) {
    c.bench_function("write_large_file", |b| {
        b.iter_with_setup(
            || {
                let temp_dir = tempfile::tempdir().unwrap();
                (temp_dir.path().to_path_buf(), temp_dir)
            },
            |(path, _temp_dir)| {
                let entry = create_test_entry(10 * 1024 * 1024, "large.bin"); // 10 MB
                let _ = write_entry(black_box(&entry), black_box(&path));
            },
        )
    });
}

fn benchmark_write_nested_file(c: &mut Criterion) {
    c.bench_function("write_nested_file", |b| {
        b.iter_with_setup(
            || {
                let temp_dir = tempfile::tempdir().unwrap();
                (temp_dir.path().to_path_buf(), temp_dir)
            },
            |(path, _temp_dir)| {
                let entry = MemEntry {
                    rel_path:   PathBuf::from(
                        "deeply/nested/dir/structure/file.txt",
                    ),
                    is_dir:     false,
                    _data_size: 1024,
                    unix_mode:  Some(0o644),
                    _file_idx:  0,
                    data:       vec![42; 1024],
                };
                let _ = write_entry(black_box(&entry), black_box(&path));
            },
        )
    });
}

fn benchmark_write_directory(c: &mut Criterion) {
    c.bench_function("write_directory", |b| {
        b.iter_with_setup(
            || {
                let temp_dir = tempfile::tempdir().unwrap();
                (temp_dir.path().to_path_buf(), temp_dir)
            },
            |(path, _temp_dir)| {
                let entry = MemEntry {
                    rel_path:   PathBuf::from("mydir"),
                    is_dir:     true,
                    _data_size: 0,
                    unix_mode:  None,
                    _file_idx:  0,
                    data:       Vec::new(),
                };
                let _ = write_entry(black_box(&entry), black_box(&path));
            },
        )
    });
}

fn benchmark_write_various_sizes(c: &mut Criterion) {
    let mut group = c.benchmark_group("write_various_sizes");

    for size in
        [1024, 10 * 1024, 100 * 1024, 1024 * 1024, 5 * 1024 * 1024].iter()
    {
        group.bench_with_input(
            BenchmarkId::from_parameter(format!("{}B", size)),
            size,
            |b, &size| {
                b.iter_with_setup(
                    || {
                        let temp_dir = tempfile::tempdir().unwrap();
                        (temp_dir.path().to_path_buf(), temp_dir)
                    },
                    |(path, _temp_dir)| {
                        let entry = create_test_entry(size, "test.bin");
                        let _ =
                            write_entry(black_box(&entry), black_box(&path));
                    },
                )
            },
        );
    }

    group.finish();
}

criterion_group!(
    benches,
    benchmark_write_small_file,
    benchmark_write_medium_file,
    benchmark_write_large_file,
    benchmark_write_nested_file,
    benchmark_write_directory,
    benchmark_write_various_sizes,
);

criterion_main!(benches);
