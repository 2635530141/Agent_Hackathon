from .runtime import get_runtime


if __name__ == "__main__":
    runtime = get_runtime()
    print("Synthetic calendar schema/seed ready; existing edits preserved. No Google access.")
    runtime.repository.close()
