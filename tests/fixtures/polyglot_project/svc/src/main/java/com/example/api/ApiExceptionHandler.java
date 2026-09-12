package com.example.api;

import org.springframework.web.bind.annotation.ControllerAdvice;

@ControllerAdvice
public class ApiExceptionHandler {
    public String handle(Exception e) {
        return e.getMessage();
    }
}
