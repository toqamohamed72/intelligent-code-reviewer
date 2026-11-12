import java.io.*;

public class Calculator {
    public int divide(int a, int b) {
        return a / b;
    }

    public boolean isAdmin(String role) {
        return role == "admin";
    }

    public String readFirstLine(String path) throws IOException {
        BufferedReader reader = new BufferedReader(new FileReader(path));
        String line = reader.readLine();
        return line.trim();
    }
}
