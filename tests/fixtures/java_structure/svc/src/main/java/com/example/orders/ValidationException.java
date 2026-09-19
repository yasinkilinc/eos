package com.example.orders;

public class ValidationException extends RuntimeException {
    private final String code;

    public ValidationException(String code, String message) {
        super(message);
        this.code = code;
    }

    public static ValidationException of(String code, String message) {
        return new ValidationException(code, message);
    }

    public String code() { return code; }
}
