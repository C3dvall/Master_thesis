import pandas as pd
import numpy as np

df = pd.read_excel("sum.xlsx")

preds = df.iloc[:, 0].values
truth = df.iloc[:, 1].values

# These must match the values in your Excel file
classes = ["human_activity", "traffic", "construction", "alarm"]
n = len(classes)

# These are the names shown in the figure
display_classes = ["human_activity", "traffic", "construction", "emergency"]

confusion_matrix = np.zeros((n, n), dtype=int)

for t, p in zip(truth, preds):
    i = classes.index(t)
    j = classes.index(p)
    confusion_matrix[i, j] += 1

accuracy_matrix = confusion_matrix / confusion_matrix.sum(axis=1, keepdims=True)

import matplotlib.pyplot as plt

plt.figure(figsize=(7, 6))
plt.imshow(accuracy_matrix, cmap="Blues", vmin=0, vmax=1)

plt.colorbar(label="Accuracy")

plt.xticks(np.arange(n), display_classes, rotation=45)
plt.yticks(np.arange(n), display_classes)

# Annotate each cell
for i in range(n):
    for j in range(n):
        value = accuracy_matrix[i, j]
        plt.text(
            j, i, f"{value:.2f}",
            ha="center",
            va="center",
            color="black"
        )

plt.title("Classification Accuracy Matrix")
plt.tight_layout()
plt.show()

# Overall accuracy
overall_accuracy = np.trace(confusion_matrix) / confusion_matrix.sum()
print("Overall accuracy:", overall_accuracy)