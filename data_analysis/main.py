import pandas as pd

# Class to load any dataset
class DataLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load_data(self) -> pd.DataFrame:
        """Load data from a CSV file into a pandas DataFrame."""
        try:
            data = pd.read_csv(self.file_path)
            return data
        except FileNotFoundError:
            print(f"Error: The file at {self.file_path} was not found.")
            return pd.DataFrame()
        except pd.errors.EmptyDataError:
            print("Error: The file is empty.")
            return pd.DataFrame()
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return pd.DataFrame()


class DataAnalysis:
    """
    Simple data analysis helper that loads a dataset and exposes a few actions
    (analyze, list_columns) which can be selected interactively.
    """

    def __init__(self, data_file: str):
        # initialize the data loader and load the data immediately
        self.data_loader = DataLoader(data_file)
        self.data = self.data_loader.load_data()

        # mapping of command names to bound methods for easy selection
        self.functions = {
            "1. analyze": self.analyze,
            "2. list_columns": self.list_columns,
            "3. preview_rows": self.preview_rows,
            "4. preview_column": self.preview_column,
            "5. preview_multiple_columns": self.preview_multiple_columns,
        }
        self.rows = 5
    def printer(self, message: str | list | dict, output_type: str = "plain") -> None:
        """
        Print messages in different formats.
        
        Args:
            message: The message/data to print
            output_type: Format type ("plain", "table", "json", "info", "warning", "error")
        """
        types = {
            "plain": lambda msg: print(msg),
            "table": lambda msg: print(pd.DataFrame(msg) if isinstance(msg, (list, dict)) else msg),
            "json": lambda msg: print(pd.DataFrame(msg).to_json(indent=2) if isinstance(msg, (list, dict)) else msg),
            "info": lambda msg: print(f"{msg}"),
            "warning": lambda msg: print(f"\033[93m[WARNING] {msg}\033[0m"),
            "error": lambda msg: print(f"\033[91m[ERROR] {msg}\033[0m")
        }
        
        if output_type in types:
            types[output_type](message)
        else:
            print(f"Unknown format '{output_type}'. Using plain format.")
            print(message)
    
    def analyze(self):
        # Run basic analysis if data is present
        if self.data.empty:
            self.printer("No data to analyze.", "error")
            return

        # Example analysis: print basic descriptive statistics for numeric columns
        self.printer("Basic Statistics:", "info")
        self.printer(self.data.describe().to_json(indent=2), "json")

    def list_columns(self):
        # Print the column names if data is loaded
        if self.data.empty:
            self.printer("No data loaded.", "error")
            return
        self.printer("Columns in the dataset:", "info")
        # Show columns in a table format
        self.printer([{"column": col} for col in self.data.columns.tolist()], "table")

    def function_selector(self, choice: str):
        # Lookup the chosen function in the dictionary and call it if found
        func = self.functions.get(choice)
        if func:
            func()
        else:
            # Inform the user of available options if the choice is invalid
            self.printer(f"Function '{choice}' not found. Available functions: {', '.join(self.functions.keys())}", "error")
    def preview_rows(self):
        num_rows = self.rows
        # Display  few rows of the dataset
        if self.data.empty:
            self.printer("No data loaded.", "error")
            return
        self.printer(f"Displaying {num_rows} rows of the dataset:", "info")
        self.printer(self.data.head(num_rows).to_dict('records'), "table")
    
    def preview_column(self):
        num_rows = self.rows
        column_name = input("Enter the column name to preview: ").strip()
        # Display  few entries of a specific column
        if self.data.empty:
            self.printer("No data loaded.", "error")
            return
        if column_name not in self.data.columns:
            self.printer(f"Column '{column_name}' not found in the dataset.", "error")
            return
        self.printer(f"Displaying {num_rows} entries of column '{column_name}':", "info")
        self.printer([{"value": val} for val in self.data[column_name].head(num_rows)], "table")

    def preview_multiple_columns(self):
        num_rows = self.rows
        column_names = input("Enter the column names to preview (comma-separated): ").strip().split(",")
        column_names = [name.strip() for name in column_names]
        # Display  few entries of specific columns
        if self.data.empty:
            self.printer("No data loaded.", "error")
            return
        missing_columns = [name for name in column_names if name not in self.data.columns]
        if missing_columns:
            self.printer(f"Columns '{', '.join(missing_columns)}' not found in the dataset.", "error")
            return
        self.printer(f"Displaying {num_rows} entries of columns '{', '.join(column_names)}':", "info")
        first_col = column_names[0]
        result = self.data[column_names].head(num_rows).set_index(first_col).to_dict('index')
        self.printer(result, "json")
    def run(self):
            # Simple interactive prompt to select which action to run
            exiting = False
            while not exiting:
                self.printer("Select a function to run:", "info")
                self.printer("To exit, type 'exit' or 'quit'.", "info")

                keys = list(self.functions.keys())
                # build maps: number -> full key, simple name -> full key
                number_map = {str(i): key for i, key in enumerate(keys, 1)}
                name_map = {}
                for key in keys:
                    parts = key.split(". ", 1)
                    simple_name = parts[1] if len(parts) > 1 else key
                    name_map[simple_name.lower()] = key

                # display a clean menu (show number + simple name)
                menu_items = []
                for i, key in enumerate(keys, 1):
                    parts = key.split(". ", 1)
                    simple_name = parts[1] if len(parts) > 1 else key
                    menu_items.append(f"{i}. {simple_name}")
                self.printer("\n".join(menu_items), "plain")

                raw = input("Enter function number or name: ").strip()
                choice = raw

                # Normalize numeric inputs like "1", "1.", "1. analyze"
                lowered = raw.lower().rstrip(".")
                if lowered.isdigit():
                    choice = number_map.get(lowered, raw)
                else:
                    # if user typed "1. analyze" or "1 analyze", try to extract leading number
                    try:
                        first_token = lowered.split()[0].rstrip(".")
                        if first_token.isdigit() and first_token in number_map:
                            choice = number_map[first_token]
                        elif lowered in name_map:
                            choice = name_map[lowered]
                        else:
                            # try exact full-key match ignoring case
                            for k in keys:
                                if lowered == k.lower():
                                    choice = k
                                    break
                    except Exception:
                        choice = raw

                if choice.lower() in ("exit", "quit"):
                    self.printer("Exiting.", "info")
                    exiting = True
                else:
                    # Only prompt for number of rows when a preview function was selected
                    lc = choice.lower().strip()
                    if "preview" in lc or lc.startswith(("3", "4", "5")):
                        rows_input = input("Enter number of rows to preview (press Enter for default 5, 'all' for all rows): ").strip()
                        if rows_input.lower() == 'all':
                            self.rows = len(self.data)
                        elif rows_input:
                            try:
                                self.rows = int(rows_input)
                            except ValueError:
                                self.printer("Invalid number, using default of 5 rows", "warning")
                                self.rows = 5
                        else:
                            self.rows = 5  # Reset to default if no input
                    self.function_selector(choice)

if __name__ == "__main__":
    data_folder = "data/"
    #list all csv files in the data folder
    import os
    csv_files = [f for f in os.listdir(data_folder) if f.endswith('.csv')]
    print("Available CSV files:")
    for i, file in enumerate(csv_files, 1):
        print(f"{i}. {file}")
    file_choice = input("Enter the number of the CSV file to load: ").strip()
    try:
        file_index = int(file_choice) - 1
        if 0 <= file_index < len(csv_files):
            data_file = os.path.join(data_folder, csv_files[file_index])
        else:
            print("Invalid selection. Exiting.")
            exit(1)
    except ValueError:
        print("Invalid input. Exiting.")
        exit(1)

    # Create the analysis object and start interactive mode
    analysis = DataAnalysis(data_file)
    analysis.run()