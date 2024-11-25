# **Hybrid Chess AI using CNN, MCTS, and Opening Book**

## **Overview**

This project is the implementation of a **hybrid chess artificial intelligence** designed to combine advanced deep learning methods and classical search algorithms. The AI utilizes a **Convolutional Neural Network (CNN)** for board evaluation, **Monte Carlo Tree Search (MCTS)** for move selection, and an **opening book** for optimized early-game play. It also features a **Graphical User Interface (GUI)** for interactive gameplay.

### **Purpose**
This project is developed as part of a thesis and serves as an educational and research tool to explore the intersection of neural networks and traditional chess AI techniques.

### **Disclaimer**
The code in this repository is for **non-commercial use only**.  
It **may not be used, reproduced, or distributed for any commercial purposes**, and it **may not be used in academic works, such as theses or research papers, without prior written consent from the author**. Please refer to the [LICENSE](LICENSE) file for details.

---

## **System Requirements**
To run this project, you need:
- **Python 3.12**
- **Anaconda**

---

## **Installation**

Follow these steps to set up the environment and run the project:

1. **Clone the Repository**:
   ```bash
   git clone <project-repo-url>
   cd <project-location>
   ```

2. **Create and Activate the Environment**:
   ```bash
   conda env create -f environment.yml
   conda activate hybrid_chess_ai
   ```

3. **Run the Application**:
   Launch the main script to start the GUI:
   ```bash
   python -m src.gui.main
   ```
