// Standalone Java validation example for Phase 1.
// This is not wired into Flask yet; integration can be assessed in a later phase.
public class VisitorValidator {
    public static boolean isValidIndianMobile(String mobile) {
        if (mobile == null) return false;
        return mobile.matches("^[6-9][0-9]{9}$");
    }

    public static void main(String[] args) {
        String testNumber = args.length > 0 ? args[0] : "9876543210";
        System.out.println(isValidIndianMobile(testNumber) ? "Valid mobile format" : "Invalid mobile format");
    }
}
