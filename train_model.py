import os
import pickle
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split

ROOT_DIR = os.path.dirname(__file__)
DATASET_PATH = os.path.join(ROOT_DIR, "dataset.csv")
MODEL_PATH = os.path.join(ROOT_DIR, "model.pkl")


def main():
    df = pd.read_csv(DATASET_PATH)
    X = df[["days_since_purchase", "storage_temp"]]
    y = df["shelf_life_left"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    model = LinearRegression()
    model.fit(X_train, y_train)

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)

    score = model.score(X_test, y_test)
    print(f"Model trained and saved to {MODEL_PATH}")
    print(f"R^2 score on test set: {score:.3f}")
    sample = model.predict([[2, 10]])[0]
    print(f"Sample prediction for days_since_purchase=2, storage_temp=10: {sample:.2f} days")


if __name__ == "__main__":
    main()
