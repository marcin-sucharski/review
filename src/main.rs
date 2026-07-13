fn main() {
    std::process::exit(review::cli::run(std::env::args_os().skip(1)));
}
